"""Recovery proof uses real SQLite files, including a WAL write."""

import sqlite3
from pathlib import Path

import pytest

from backend.app.config import settings
from backend.app.workspace_store import connect, init_workspace
from scripts.workspace_backup import copy_workspace


def test_backup_restore_preserves_workspace_and_suppressions(tmp_path):
    init_workspace()
    with connect() as conn:
        conn.execute("INSERT INTO profile VALUES (1, ?)", ('{"company_name":"Synthetic"}',))
        conn.execute(
            "INSERT INTO suppressions VALUES (?, ?)", ("blocked@example.test", "2026-09-12")
        )
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    counts = copy_workspace(Path(settings.workspace_db_path), backup)
    assert counts["suppressions"] == 1
    assert copy_workspace(backup, restored) == counts
    with sqlite3.connect(restored) as conn:
        assert (
            conn.execute("SELECT email FROM suppressions").fetchone()[0] == "blocked@example.test"
        )
        assert (
            conn.execute("SELECT data FROM profile").fetchone()[0] == '{"company_name":"Synthetic"}'
        )
    assert backup.stat().st_mode & 0o777 == 0o600


def test_restore_refuses_overwrite(tmp_path):
    init_workspace()
    destination = tmp_path / "existing.db"
    destination.write_text("do not replace")
    with pytest.raises(FileExistsError):
        copy_workspace(Path(settings.workspace_db_path), destination)
    assert destination.read_text() == "do not replace"


def test_backup_refuses_non_workspace(tmp_path):
    source = tmp_path / "other.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
    destination = tmp_path / "backup.db"
    with pytest.raises(ValueError, match="Not an Outreached"):
        copy_workspace(source, destination)
    assert not destination.exists()
