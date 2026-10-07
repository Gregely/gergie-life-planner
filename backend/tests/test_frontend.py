"""The PWA is served by the same app as the API (stage 2)."""

import json

from fastapi.testclient import TestClient

from mealplanner.main import create_app


def test_index_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert '<script src="/app.js"' in resp.text
    assert 'rel="manifest"' in resp.text


def test_assets_served(client):
    for path, kind in [("/app.js", "javascript"), ("/sw.js", "javascript"), ("/style.css", "text/css"), ("/icons/icon-192.png", "image/png")]:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert kind in resp.headers["content-type"], path


def test_manifest_is_installable(client):
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert "manifest+json" in resp.headers["content-type"]
    manifest = json.loads(resp.text)
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_service_worker_caches_every_shell_file(client):
    sw = client.get("/sw.js").text
    shell = sw.split("const SHELL = [", 1)[1].split("]", 1)[0]
    for path in [p.strip().strip("'") for p in shell.split(",")]:
        assert client.get(path).status_code == 200, path


def test_api_routes_still_take_precedence(client):
    assert client.get("/api/health").json()["status"] == "ok"
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404


def test_api_works_without_frontend(tmp_path):
    client = TestClient(create_app(tmp_path / "x.db", frontend_dir=tmp_path / "missing"))
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 404
