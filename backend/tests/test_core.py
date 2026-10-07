"""Core infrastructure: migrations, module discovery, app wiring."""

import sqlite3
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from mealplanner.core.db import Migration, apply_migrations, connect
from mealplanner.core.module import discover_modules
from mealplanner.main import create_app


def test_migrations_recorded_per_owner_and_idempotent(db_path):
    create_app(db_path)
    create_app(db_path)  # restarting must not re-run anything
    rows = sqlite3.connect(db_path).execute("SELECT owner, version FROM schema_migrations ORDER BY owner").fetchall()
    assert rows == [("core", 1), ("eaten", 1)]


def test_new_migration_versions_apply_incrementally(tmp_path):
    conn = connect(tmp_path / "m.db")
    assert apply_migrations(conn, "demo", [Migration(1, "CREATE TABLE a (x);")]) == [1]
    assert apply_migrations(conn, "demo", [Migration(1, "CREATE TABLE a (x);"), Migration(2, "CREATE TABLE b (y);")]) == [2]
    assert apply_migrations(conn, "demo", [Migration(1, "x"), Migration(2, "y")]) == []


def test_failed_migration_rolls_back(tmp_path):
    conn = connect(tmp_path / "m.db")
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(conn, "demo", [Migration(1, "CREATE TABLE ok (x); CREATE TABLE broken (;")])
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'ok'").fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0


def test_health_lists_discovered_modules(client):
    assert set(client.get("/api/health").json()["modules"]) == {"eaten", "ingredients", "pantry", "plan", "recipes", "shopping"}


def test_foreign_keys_enforced(db_path):
    create_app(db_path)
    conn = connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO pantry (ingredient_id, quantity) VALUES (999, 1)")


def test_new_module_is_discovered_without_editing_existing_code(tmp_path, monkeypatch):
    """Drop a package with a router and a migration in; it is mounted and migrated."""
    pkg = tmp_path / "fakemods"
    (pkg / "notes").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "notes" / "__init__.py").write_text(textwrap.dedent('''
        from fastapi import APIRouter
        from mealplanner.core.db import Migration
        from mealplanner.core.deps import Conn
        from mealplanner.core.module import Module

        router = APIRouter(prefix="/notes")

        @router.get("")
        def count(conn: Conn) -> dict:
            return {"count": conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]}

        module = Module(name="notes", router=router, migrations=[Migration(1, "CREATE TABLE notes (body TEXT);")])
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    import fakemods

    import mealplanner.main as main

    real = main.discover_modules
    monkeypatch.setattr(main, "discover_modules", lambda package: real(package) + discover_modules(fakemods))
    client = TestClient(create_app(tmp_path / "x.db"))
    assert "notes" in client.get("/api/health").json()["modules"]
    assert client.get("/api/notes").json() == {"count": 0}
    sys.modules.pop("fakemods", None)
    sys.modules.pop("fakemods.notes", None)
