"""scripts/backup.py: consistent snapshots of a live database, dated names, pruning."""

import datetime as dt
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from mealplanner.core.db import connect
from mealplanner.main import create_app

_spec = importlib.util.spec_from_file_location("backup", Path(__file__).resolve().parent.parent / "scripts" / "backup.py")
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)


def names(conn):
    return [r[0] for r in conn.execute("SELECT name FROM ingredients ORDER BY name")]


@pytest.fixture
def live_db(db_path):
    create_app(db_path)
    conn = connect(db_path)  # WAL mode, like the running app
    conn.execute("INSERT INTO ingredients (name, unit) VALUES ('Flour', 'g')")
    yield db_path, conn
    conn.close()


def test_backup_while_app_is_running_includes_committed_wal_data(live_db, tmp_path):
    db_path, conn = live_db
    assert Path(f"{db_path}-wal").exists()  # data still sitting in the WAL, not checkpointed
    target = backup.backup(db_path, tmp_path / "backups")
    with sqlite3.connect(target) as copy:
        assert names(copy) == ["Flour"]
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_backup_during_open_write_transaction_skips_uncommitted(live_db, tmp_path):
    db_path, conn = live_db
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO ingredients (name, unit) VALUES ('Half-written', 'g')")
    target = backup.backup(db_path, tmp_path / "backups")  # must not block or fail
    conn.execute("ROLLBACK")
    with sqlite3.connect(target) as copy:
        assert names(copy) == ["Flour"]


def test_backup_is_a_working_database(live_db, tmp_path):
    from fastapi.testclient import TestClient

    db_path, _ = live_db
    target = backup.backup(db_path, tmp_path / "backups")
    restored = TestClient(create_app(target))  # restoring = pointing the app at the file
    assert [i["name"] for i in restored.get("/api/ingredients").json()] == ["Flour"]


def test_dated_name_and_no_partial_files(live_db, tmp_path):
    db_path, _ = live_db
    dest = tmp_path / "backups"
    target = backup.backup(db_path, dest, now=dt.datetime(2026, 10, 7, 3, 15, 0))
    assert target.name == "mealplanner-2026-10-07_031500.db"
    assert [p.name for p in dest.iterdir()] == [target.name]


def test_missing_database_fails_without_creating_one(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        backup.backup(missing, tmp_path / "backups")
    assert not missing.exists()
    assert backup.main(["--db", str(missing), "--dest", str(tmp_path / "backups")]) == 1


def test_prune_keeps_newest_and_ignores_other_files(live_db, tmp_path):
    db_path, _ = live_db
    dest = tmp_path / "backups"
    made = [backup.backup(db_path, dest, now=dt.datetime(2026, 10, d, 3, 0)) for d in (1, 2, 3, 4)]
    (dest / "notes.txt").write_text("keep me")
    removed = backup.prune(dest, keep=2)
    assert removed == made[:2]
    assert sorted(p.name for p in dest.iterdir()) == sorted([made[2].name, made[3].name, "notes.txt"])
    assert backup.prune(dest, keep=0) == []  # 0 = keep everything


def test_main_cli(live_db, tmp_path, capsys):
    db_path, _ = live_db
    dest = tmp_path / "backups"
    assert backup.main(["--db", str(db_path), "--dest", str(dest), "--keep", "5"]) == 0
    assert "backed up" in capsys.readouterr().out
    assert len(list(dest.glob("mealplanner-*.db"))) == 1


def test_env_vars_are_defaults(live_db, tmp_path, monkeypatch):
    db_path, _ = live_db
    monkeypatch.setenv("MEALPLANNER_DB", str(db_path))
    monkeypatch.setenv("MEALPLANNER_BACKUP_DIR", str(tmp_path / "env-backups"))
    assert backup.main([]) == 0
    assert len(list((tmp_path / "env-backups").glob("*.db"))) == 1
