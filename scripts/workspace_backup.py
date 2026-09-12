"""Consistent SQLite backups and restoration to a NEW file; never overwrite data."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

REQUIRED_TABLES = {"profile", "leads", "suppressions", "campaigns", "drafts", "events"}


def check_workspace(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed")
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not tables >= REQUIRED_TABLES:
        raise ValueError("Not an Outreached workspace database")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Workspace contains invalid references")


def copy_workspace(source: Path, destination: Path) -> dict[str, int]:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if source == destination.resolve():
        raise ValueError("Source and destination must be different")
    # Read-only source, atomic exclusive destination, private file permissions.
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original:
        check_workspace(original)
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with closing(sqlite3.connect(destination)) as backup:
                original.backup(backup)
                check_workspace(backup)
                counts = {
                    table: backup.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in sorted(REQUIRED_TABLES)
                }
            return counts
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, help="Must not already exist")
    args = parser.parse_args()
    counts = copy_workspace(args.source, args.destination)
    print(f"{args.operation}: integrity verified; table counts: {counts}")


if __name__ == "__main__":
    main()
