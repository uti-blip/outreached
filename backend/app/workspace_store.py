"""Durable, single-user Lexia workspace. Independent of optional paid adapters."""

import json
import re
import sqlite3
import ssl
from contextlib import contextmanager, suppress
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg2
from psycopg2.extras import DictCursor, execute_values
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from backend.app.config import settings, validate_database_url

POSTGRES_SCHEMA = "outreached_workspace"
POSTGRES_RUNTIME_ROLE = "outreached_app"
WORKSPACE_TABLES = ("profile", "leads", "suppressions", "campaigns", "drafts", "events")
WRITER_LOCK = (1869968498, 1)


class WorkspaceStoreError(RuntimeError):
    """A sanitized storage failure; never expose connection strings or row data."""


def postgres_options(url: str, *, production: bool = False, root_cert: str = "") -> dict:
    validate_database_url(url, production=production)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    options = {"connect_timeout": 5, "application_name": "outreached-workspace"}
    # Local test clusters can opt out explicitly; every remote connection is
    # verified against either the supplied CA or the operating system bundle.
    mode = query.get("sslmode", ["verify-full"])[0]
    options["sslmode"] = mode
    if mode == "verify-full":
        certificate = root_cert or query.get("sslrootcert", [""])[0]
        certificate = certificate or ssl.get_default_verify_paths().cafile
        if not certificate or not Path(certificate).is_file():
            raise WorkspaceStoreError("Le certificat racine PostgreSQL est indisponible.")
        options["sslrootcert"] = certificate
    return options


@lru_cache(maxsize=4)
def postgres_engine(url: str, production: bool, root_cert: str):
    # A small bounded pool protects free database connection limits. Pre-ping
    # replaces sockets closed while an ephemeral process or database was idle.
    normalized = "postgresql+psycopg2://" + url.split("://", 1)[1]
    return create_engine(
        normalized,
        connect_args=postgres_options(url, production=production, root_cert=root_cert),
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
        pool_recycle=300,
        hide_parameters=True,
    )


# Preserve quoted strings, identifiers and comments when translating the small
# SQLite DB-API surface used by the workspace. Values always remain parameters.
_SQL_TOKEN = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/|::|:[A-Za-z_]\w*|\?",
    re.DOTALL,
)


def postgres_query(statement: str, parameters):
    if parameters is None:
        return statement
    statement = statement.replace("%", "%%")

    def placeholder(match):
        token = match.group()
        if token == "?":
            return "%s"
        if token.startswith(":") and token != "::":
            return f"%({token[1:]})s"
        return token

    return _SQL_TOKEN.sub(placeholder, statement)


class PostgresWorkspace:
    is_postgres = True

    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=None):
        cursor = self.connection.cursor(cursor_factory=DictCursor)
        try:
            cursor.execute(postgres_query(statement, parameters), parameters)
        except Exception:
            cursor.close()
            raise
        return cursor


def insert_rows(conn, table: str, columns: tuple[str, ...], rows: list[tuple]):
    """Insert at most one import's rows in a parameterized batch transaction.

    Callers supply constant identifiers; enforce their shape separately from
    values, which are always adapted by the database driver. PostgreSQL uses a
    single round trip rather than DB-API executemany's per-row requests.
    """
    if (
        table not in WORKSPACE_TABLES
        or not columns
        or any(not re.fullmatch(r"[a-z_][a-z0-9_]*", column) for column in columns)
    ):
        raise ValueError("Invalid workspace insert identifiers")
    if len(rows) > 1000 or any(len(row) != len(columns) for row in rows):
        raise ValueError("Invalid workspace insert batch")
    if not rows:
        return
    names = ",".join(f'"{column}"' for column in columns)
    statement = f'INSERT INTO "{table}" ({names}) VALUES '
    if getattr(conn, "is_postgres", False):
        with conn.connection.cursor() as cursor:
            execute_values(cursor, statement + "%s", rows, page_size=1000)
    else:
        conn.executemany(statement + "(" + ",".join("?" for _ in columns) + ")", rows)


@contextmanager
def postgres_connect(url: str, *, write: bool = True):
    connection = None
    try:
        engine = postgres_engine(
            url, settings.app_env == "production", settings.workspace_database_ssl_root_cert
        )
        connection = engine.raw_connection()
        connection.set_session(
            isolation_level="READ COMMITTED" if write else "REPEATABLE READ",
            readonly=not write,
            autocommit=False,
        )
        workspace = PostgresWorkspace(connection)
        workspace.execute("SET LOCAL search_path = outreached_workspace, pg_catalog")
        workspace.execute("SET LOCAL statement_timeout = '15000ms'")
        workspace.execute("SET LOCAL lock_timeout = '5000ms'")
        workspace.execute("SET LOCAL idle_in_transaction_session_timeout = '20000ms'")
        if write:
            # READ COMMITTED is intentional: the first business read must see
            # commits made by the previous writer while this lock was waiting.
            workspace.execute("SELECT pg_advisory_xact_lock(?, ?)", WRITER_LOCK)
        yield workspace
        connection.commit()
    except (psycopg2.Error, SQLAlchemyError):
        if connection:
            with suppress(psycopg2.Error, SQLAlchemyError):
                connection.rollback()
        raise WorkspaceStoreError(
            "Le stockage PostgreSQL est temporairement indisponible."
        ) from None
    except Exception:
        if connection:
            with suppress(psycopg2.Error, SQLAlchemyError):
                connection.rollback()
        raise
    finally:
        if connection:
            connection.close()


@contextmanager
def connect(*, write: bool = True):
    """Open a transaction; readers never reserve the workspace's write lock.

    Read-only connections also refuse to create a missing database, which makes
    this safe to use for readiness checks against the configured persistent file.
    """
    if settings.workspace_database_url:
        with postgres_connect(settings.workspace_database_url, write=write) as conn:
            yield conn
        return
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
    if settings.workspace_database_url:
        # Runtime credentials cannot create or alter the schema. Provision it
        # separately with the migration owner before starting the application.
        with connect(write=False) as conn:
            row = conn.execute(
                "SELECT current_user AS role, has_schema_privilege(current_user, "
                "'outreached_workspace', 'CREATE') AS can_create, "
                "EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='outreached_workspace' "
                "AND r.rolname=current_user) AS owns_objects, "
                "(SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication "
                "FROM pg_roles WHERE rolname=current_user) AS privileged"
            ).fetchone()
            if row["role"] != POSTGRES_RUNTIME_ROLE or any(
                row[key] for key in ("can_create", "owns_objects", "privileged")
            ):
                raise WorkspaceStoreError("Utilisez le rôle PostgreSQL restreint outreached_app.")
            version = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
            if not version or version["version"] != 1:
                raise WorkspaceStoreError("La migration du stockage PostgreSQL est requise.")
            for table in WORKSPACE_TABLES:
                conn.execute(f"SELECT 1 FROM {table} LIMIT 0")
        return
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
