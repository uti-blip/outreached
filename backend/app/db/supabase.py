"""Supabase client singleton + SQLite fallback for local dev."""

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.app.config import settings

# ── Singleton ──────────────────────────────────────────
_client = None
_use_supabase = None  # tri-state: None (unchecked), True, False


def get_supabase():
    """Return Supabase client or None if unavailable."""
    global _client, _use_supabase

    if _use_supabase is not None:
        return _client if _use_supabase else None

    if not settings.supabase_url or not settings.supabase_service_key:
        _use_supabase = False
        _client = None
        return None

    try:
        from supabase import create_client

        _client = create_client(settings.supabase_url, settings.supabase_service_key)
        # Quick check: list tenants table (may fail if table doesn't exist yet)
        _use_supabase = True
        return _client
    except Exception:
        _use_supabase = False
        _client = None
        return None


# ── SQLite fallback (dev/testing) ──────────────────────
DB_PATH = Path(__file__).resolve().parent.parent.parent / "dev.db"


def _get_sqlite() -> sqlite3.Connection:
    """Get SQLite connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    """Context manager for SQLite connection."""
    conn = _get_sqlite()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Initialisation des tables SQLite ───────────────────
def _init_sqlite_tables():
    """Create all tables if they don't exist (mirrors 001_schema.sql)."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                vertical TEXT NOT NULL DEFAULT 'saas_fr',
                plan TEXT NOT NULL DEFAULT 'agency',
                stripe_customer_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS playbooks (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                name TEXT NOT NULL,
                vertical TEXT NOT NULL DEFAULT 'saas_fr',
                version INTEGER NOT NULL DEFAULT 1,
                config TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS playbook_chunks (
                id TEXT PRIMARY KEY,
                playbook_id TEXT NOT NULL REFERENCES playbooks(id),
                chunk_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                name TEXT NOT NULL,
                domain TEXT,
                firmographics TEXT NOT NULL DEFAULT '{}',
                icp_score REAL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id),
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                first_name TEXT,
                last_name TEXT,
                title TEXT,
                email TEXT,
                linkedin_url TEXT,
                gdpr_basis TEXT DEFAULT 'legitimate_interest',
                gdpr_consent_at TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                playbook_id TEXT NOT NULL REFERENCES playbooks(id),
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                config TEXT NOT NULL DEFAULT '{}',
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sequences (
                id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                contact_id TEXT NOT NULL REFERENCES contacts(id),
                steps TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL REFERENCES contacts(id),
                sequence_id TEXT REFERENCES sequences(id),
                channel TEXT NOT NULL,
                step_number INTEGER NOT NULL DEFAULT 1,
                subject TEXT,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                dry_run INTEGER NOT NULL DEFAULT 1,
                external_id TEXT,
                sent_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS replies (
                id TEXT PRIMARY KEY,
                message_id TEXT REFERENCES messages(id),
                contact_id TEXT NOT NULL REFERENCES contacts(id),
                channel TEXT NOT NULL,
                intent TEXT,
                confidence REAL,
                raw_body TEXT NOT NULL,
                agent_response TEXT,
                routed_to TEXT,
                handled_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS deals (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id),
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                stage TEXT NOT NULL DEFAULT 'discovery',
                value_eur REAL,
                closed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                campaign_id TEXT REFERENCES campaigns(id),
                agent_type TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_eur REAL NOT NULL DEFAULT 0.0,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rls_test (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                secret_data TEXT NOT NULL
            );
        """)


def _ensure_dev_tenant():
    """Ensure a default dev tenant exists for local testing."""
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM tenants LIMIT 1").fetchone()
        if not existing:
            tid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO tenants (id, name, vertical) VALUES (?, ?, ?)",
                (tid, "dev", settings.vertical),
            )
            # Create a default playbook
            pid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO playbooks (id, tenant_id, name, vertical, config) VALUES (?, ?, ?, ?, ?)",
                (pid, tid, "playbook-saas-fr-v1", "saas_fr", '{"persona":"CTO/VP Eng SaaS FR"}'),
            )
            return tid, pid
        tid = existing["id"]
        playbook = conn.execute(
            "SELECT id FROM playbooks WHERE tenant_id = ? LIMIT 1", (tid,)
        ).fetchone()
        return tid, playbook["id"] if playbook else None


# ── Public API ─────────────────────────────────────────
def init_db():
    """Initialize database (creates tables + dev seed if SQLite)."""
    if get_supabase():
        return  # Supabase — tables created via migrations

    _init_sqlite_tables()
    _ensure_dev_tenant()
