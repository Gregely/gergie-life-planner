#!/usr/bin/env python3
"""Back up the meal planner database to a dated file.

Safe while the app is running: it uses SQLite's online backup API, which
copies a consistent snapshot (including anything still in the WAL file).
A plain file copy is not safe for a live SQLite database.

Standard library only, so it runs with any Python 3.9+ on Linux, macOS or
Windows, with or without the app's virtualenv.

    python scripts/backup.py                       # defaults below
    python scripts/backup.py --keep 14             # keep the 14 newest backups
    python scripts/backup.py --db /path/app.db --dest /mnt/usb/backups

Defaults: --db is $MEALPLANNER_DB or backend/data/mealplanner.db,
--dest is $MEALPLANNER_BACKUP_DIR or backend/backups, --keep 30.
Exit code is 0 on success and 1 on failure, so cron/Task Scheduler can alert.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BACKEND_DIR / "data" / "mealplanner.db"
DEFAULT_DEST = BACKEND_DIR / "backups"
PREFIX = "mealplanner-"
SUFFIX = ".db"


def backup(db_path: Path, dest_dir: Path, now: dt.datetime | None = None) -> Path:
    """Write a consistent snapshot of ``db_path`` to ``dest_dir`` and return its path."""
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    target = dest_dir / f"{PREFIX}{stamp}{SUFFIX}"
    partial = target.with_name(target.name + ".partial")

    # Open the source read-only so a typo'd path can never create an empty database.
    source = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(partial)
        try:
            source.backup(dest)
            dest.execute("PRAGMA journal_mode = DELETE")  # a self-contained single file
            result = dest.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"backup failed integrity check: {result}")
        finally:
            dest.close()
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    os.replace(partial, target)  # only complete backups ever carry the final name
    return target


def prune(dest_dir: Path, keep: int) -> list[Path]:
    """Delete all but the ``keep`` newest backups. Returns the deleted paths."""
    if keep < 1:
        return []
    backups = sorted(dest_dir.glob(f"{PREFIX}*{SUFFIX}"))  # timestamped names sort chronologically
    doomed = backups[:-keep]
    for path in doomed:
        path.unlink()
    return doomed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the meal planner SQLite database.")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("MEALPLANNER_DB") or DEFAULT_DB))
    parser.add_argument("--dest", type=Path, default=Path(os.environ.get("MEALPLANNER_BACKUP_DIR") or DEFAULT_DEST))
    parser.add_argument("--keep", type=int, default=30, help="number of newest backups to keep (0 = keep all)")
    args = parser.parse_args(argv)

    try:
        target = backup(args.db, args.dest)
        removed = prune(args.dest, args.keep)
    except Exception as exc:  # noqa: BLE001 - report any failure and exit non-zero
        print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} backup FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} backed up {args.db} -> {target}"
          + (f" (pruned {len(removed)} old)" if removed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
