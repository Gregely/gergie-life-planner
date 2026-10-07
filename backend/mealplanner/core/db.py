"""SQLite connection handling and the migration runner.

Every owner (the core, and each feature module) supplies an ordered list of
migrations. Applied versions are recorded per owner in ``schema_migrations``,
so modules can add tables without touching anyone else's schema.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the settings the app relies on.

    ``isolation_level=None`` puts sqlite3 in autocommit mode; multi-statement
    writes are grouped explicitly with :func:`transaction`.
    """
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically: commit on success, roll back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            owner      TEXT    NOT NULL,
            version    INTEGER NOT NULL,
            applied_at TEXT    NOT NULL,
            PRIMARY KEY (owner, version)
        )
        """
    )


def apply_migrations(conn: sqlite3.Connection, owner: str, migrations: Sequence[Migration]) -> list[int]:
    """Apply any of ``owner``'s migrations not yet recorded. Returns versions applied."""
    _ensure_migrations_table(conn)
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration versions for {owner!r}")
    applied = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations WHERE owner = ?", (owner,))
    }
    done: list[int] = []
    for migration in sorted(migrations, key=lambda m: m.version):
        if migration.version in applied:
            continue
        # executescript() cannot take parameters, so the bookkeeping insert is
        # done inside the same explicit transaction instead.
        try:
            conn.executescript("BEGIN;\n" + migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (owner, version, applied_at) VALUES (?, ?, ?)",
                (owner, migration.version, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        done.append(migration.version)
    return done
