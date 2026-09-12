"""Durable, single-user Lexia workspace. Independent of optional paid adapters."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from backend.app.config import settings


@contextmanager
def connect(*, write: bool = True):
    """Open a transaction; readers never reserve the workspace's write lock.

    Read-only connections also refuse to create a missing database, which makes
    this safe to use for readiness checks against the configured persistent file.
    """
    path = Path(settings.workspace_db_path)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
    else:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        if write:
            conn.execute("PRAGMA journal_mode=WAL")
        # Serialize writers, including check-then-insert deduplication. Readers
        # retain a consistent WAL snapshot while allowing writes to proceed.
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_workspace():
    with connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leads (
                id TEXT PRIMARY KEY, company_name TEXT NOT NULL, siren TEXT NOT NULL DEFAULT '',
                contact_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
                activity TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
                qualified INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS lead_email ON leads(email) WHERE email <> '';
            CREATE UNIQUE INDEX IF NOT EXISTS lead_siren ON leads(siren) WHERE siren <> '';
            CREATE TABLE IF NOT EXISTS suppressions (
                email TEXT PRIMARY KEY, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, profile TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
                step INTEGER NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                footer TEXT NOT NULL, sent_at TEXT,
                UNIQUE(lead_id, step)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
        """)


def get_profile(conn):
    row = conn.execute("SELECT data FROM profile WHERE id=1").fetchone()
    return json.loads(row["data"]) if row else None
