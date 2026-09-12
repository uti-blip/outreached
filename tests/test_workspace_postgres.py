"""Real PostgreSQL integration, restricted to a disposable loopback test DB."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg2
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, settings, validate_database_url
from backend.app.main import create_app
from backend.app.prospecting import LeadInput, add_lead
from backend.app.workspace_store import WorkspaceStoreError, connect, init_workspace, postgres_query
from scripts.workspace_postgres import backup, migrate, read_backup, restore
from tests.test_prospecting import add, sequence, update

RUNTIME_PASSWORD = "synthetic-integration-password-123456789"


@pytest.fixture
def postgres_workspace(monkeypatch):
    url = os.environ.get("WORKSPACE_TEST_POSTGRES_URL")
    if not url:
        pytest.skip(
            "WORKSPACE_TEST_POSTGRES_URL requires a disposable loopback PostgreSQL database"
        )
    parsed = urlsplit(url)
    if (
        parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.path != "/outreached_test"
    ):
        pytest.fail("PostgreSQL integration tests only accept loopback /outreached_test")
    with closing(psycopg2.connect(url)) as connection, connection, connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS outreached_workspace CASCADE")
    migrate(url, RUNTIME_PASSWORD)
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    runtime_url = urlunsplit(
        (
            parsed.scheme,
            f"outreached_app:{quote(RUNTIME_PASSWORD)}@{host}:{parsed.port or 5432}",
            parsed.path,
            parsed.query,
            "",
        )
    )
    monkeypatch.setattr(settings, "workspace_database_url", runtime_url)
    init_workspace()
    return {"owner": url, "runtime": runtime_url}


def test_real_postgres_manual_workflow_and_suppression(postgres_workspace):
    with TestClient(create_app()) as client:
        lead = add(client, company_name="Équipe ? 100% :named 'quoted'", qualified=True)
        drafts = sequence(client, lead)
        prepared = client.post(f"/api/drafts/{drafts[0]['id']}/prepare")
        assert prepared.status_code == 200
        assert "STOP" in prepared.json()["body"]
        assert client.post(f"/api/drafts/{drafts[0]['id']}/mark-sent").status_code == 200
        assert client.post(f"/api/drafts/{drafts[0]['id']}/mark-sent").status_code == 409
        assert client.post(f"/api/drafts/{drafts[1]['id']}/prepare").status_code == 409
        assert update(client, lead, status="do_not_contact").status_code == 200
        assert client.post(f"/api/drafts/{drafts[1]['id']}/prepare").status_code == 409
        assert update(client, lead, status="new").status_code == 409
        exported = client.get("/api/export/backup.json").json()
        assert exported["data"]["suppressions"][0]["email"] == lead["email"]
        workspace = client.get("/api/workspace").json()
        assert workspace["leads"][0]["qualified"] is True
        assert workspace["leads"][0]["sent_count"] == 1
        assert workspace["campaigns"][0]["drafts"] == 3
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/export/leads.csv").status_code == 200
    with TestClient(create_app()) as restarted:
        assert restarted.get("/api/workspace").json()["leads"][0]["id"] == lead["id"]


def test_real_postgres_concurrent_deduplication_and_snapshot(postgres_workspace):
    def create():
        with connect() as conn:
            return add_lead(conn, LeadInput(company_name="Same", email="same@example.test"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: create(), range(16)))
    assert sum(created for _, created in results) == 1
    assert len({lead_id for lead_id, _ in results}) == 1

    def insert_profile():
        with connect() as conn:
            conn.execute("INSERT INTO profile(id,data) VALUES(1,?)", ('{"company_name":"Next"}',))

    with ThreadPoolExecutor(max_workers=1) as pool, connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0
        pool.submit(insert_profile).result(timeout=5)
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0
    with connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 1
    with pytest.raises(WorkspaceStoreError), connect(write=False) as conn:
        conn.execute("INSERT INTO profile(id,data) VALUES (1,'{}')")


def test_postgres_runtime_cannot_own_schema_delete_or_change_metadata(
    postgres_workspace, monkeypatch
):
    for statement in (
        "CREATE TABLE outreached_workspace.forbidden(id INTEGER)",
        "DELETE FROM leads",
        "UPDATE schema_version SET version=2 WHERE id=1",
    ):
        with pytest.raises(WorkspaceStoreError), connect() as conn:
            conn.execute(statement)
    monkeypatch.setattr(settings, "workspace_database_url", postgres_workspace["owner"])
    with pytest.raises(WorkspaceStoreError, match="restreint"):
        init_workspace()


def test_private_schema_denies_untrusted_roles(postgres_workspace):
    with (
        closing(psycopg2.connect(postgres_workspace["owner"])) as conn,
        conn,
        conn.cursor() as cursor,
    ):
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname='outreached_test_visitor'")
        if not cursor.fetchone():
            cursor.execute("CREATE ROLE outreached_test_visitor NOLOGIN")
        cursor.execute("SET LOCAL ROLE outreached_test_visitor")
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cursor.execute("SELECT * FROM outreached_workspace.leads")
        conn.rollback()


def test_readiness_fails_without_schema_instead_of_falling_back(postgres_workspace):
    with TestClient(create_app()) as client:
        with (
            closing(psycopg2.connect(postgres_workspace["owner"])) as conn,
            conn,
            conn.cursor() as cursor,
        ):
            cursor.execute("DROP SCHEMA outreached_workspace CASCADE")
        assert client.get("/health/ready").status_code == 503
    assert not Path(settings.workspace_db_path).exists()


def test_sqlite_import_postgres_backup_and_restore_preserve_history(
    postgres_workspace, monkeypatch, tmp_path
):
    runtime = settings.workspace_database_url
    monkeypatch.setattr(settings, "workspace_database_url", "")
    with TestClient(create_app()) as client:
        lead = add(client)
        drafts = sequence(client, lead)
        assert client.post(f"/api/drafts/{drafts[0]['id']}/mark-sent").status_code == 200
        assert update(client, lead, status="do_not_contact").status_code == 200
    source = Path(settings.workspace_db_path)
    original = read_backup(source)
    monkeypatch.setattr(settings, "workspace_database_url", runtime)
    counts = restore(source, postgres_url=postgres_workspace["owner"])
    assert counts["suppressions"] == 1 and counts["drafts"] == 3
    destination = tmp_path / "postgres-backup.json"
    assert backup(destination) == counts
    assert read_backup(destination) == original
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        backup(destination)
    with pytest.raises(ValueError, match="contient déjà"):
        restore(destination, postgres_url=postgres_workspace["owner"])
    with TestClient(create_app()) as client:
        another = add(client, email="next@example.test", company_name="After restore")
        events = client.get(f"/api/leads/{another['id']}/detail").json()["events"]
        assert events[0]["id"] > max(event["id"] for event in original["events"])
    # The same PostgreSQL snapshot can be restored to a fresh SQLite workspace.
    monkeypatch.setattr(settings, "workspace_database_url", "")
    monkeypatch.setattr(settings, "workspace_db_path", str(tmp_path / "restored-sqlite.db"))
    init_workspace()
    assert restore(destination) == counts
    assert read_backup(Path(settings.workspace_db_path)) == original


def test_restore_rolls_back_all_rows_on_broken_reference(postgres_workspace, tmp_path):
    data = {
        table: [] for table in ("profile", "leads", "suppressions", "campaigns", "drafts", "events")
    }
    data["profile"] = [{"id": 1, "data": "{}"}]
    data["events"] = [
        {
            "id": 1,
            "lead_id": "missing",
            "kind": "created",
            "detail": "invalid",
            "created_at": "2026-09-12",
        }
    ]
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps({"version": 1, "data": data}))
    with pytest.raises(WorkspaceStoreError):
        restore(source, postgres_url=postgres_workspace["owner"])
    with connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0


def test_distributed_login_state_is_bounded_private_and_excluded_from_business_backups(
    postgres_workspace, tmp_path
):
    state = {"v": 1, "global": {"count": 1, "reset": 1}, "clients": {}}
    with connect() as conn:
        assert conn.execute("SELECT state FROM login_rate_limit WHERE id=1").fetchone()[0] == {}
        conn.execute("UPDATE login_rate_limit SET state=?::jsonb WHERE id=1", (json.dumps(state),))
    migrate(postgres_workspace["owner"])
    with connect(write=False) as conn:
        assert conn.execute("SELECT state FROM login_rate_limit WHERE id=1").fetchone()[0] == state
    destination = tmp_path / "business-backup.json"
    counts = backup(destination)
    assert "login_rate_limit" not in counts
    assert "login_rate_limit" not in json.loads(destination.read_text())["data"]
    for statement, values in (
        ("DELETE FROM login_rate_limit", None),
        ("INSERT INTO login_rate_limit(id,state) VALUES(2,'{}')", None),
        ("UPDATE login_rate_limit SET state='[]'::jsonb WHERE id=1", None),
        (
            "UPDATE login_rate_limit SET state=?::jsonb WHERE id=1",
            (json.dumps({"oversized": "x" * 262144}),),
        ),
    ):
        with pytest.raises(WorkspaceStoreError), connect() as conn:
            conn.execute(statement, values)


def test_migration_is_idempotent_and_does_not_rotate_runtime_password(postgres_workspace):
    migrate(postgres_workspace["owner"])
    init_workspace()
    with connect(write=False) as conn:
        assert conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0] == 1


def test_migration_removes_excess_runtime_grants(postgres_workspace):
    with (
        closing(psycopg2.connect(postgres_workspace["owner"])) as conn,
        conn,
        conn.cursor() as cursor,
    ):
        cursor.execute("GRANT CREATE ON SCHEMA outreached_workspace TO outreached_app")
        cursor.execute("GRANT DELETE,TRUNCATE ON outreached_workspace.leads TO outreached_app")
        cursor.execute(
            "GRANT UPDATE ON SEQUENCE outreached_workspace.events_id_seq TO outreached_app"
        )
    migrate(postgres_workspace["owner"])
    init_workspace()
    for statement in (
        "DELETE FROM leads",
        "TRUNCATE leads CASCADE",
        "SELECT setval('outreached_workspace.events_id_seq', 50)",
    ):
        with pytest.raises(WorkspaceStoreError), connect() as conn:
            conn.execute(statement)


def test_migration_refuses_runtime_ownership(postgres_workspace):
    with (
        closing(psycopg2.connect(postgres_workspace["owner"])) as conn,
        conn,
        conn.cursor() as cursor,
    ):
        cursor.execute("ALTER TABLE outreached_workspace.leads OWNER TO outreached_app")
    with pytest.raises(ValueError, match="posséder"):
        migrate(postgres_workspace["owner"])
    with pytest.raises(WorkspaceStoreError, match="restreint"):
        init_workspace()


def test_migration_and_startup_refuse_runtime_replication(postgres_workspace):
    with (
        closing(psycopg2.connect(postgres_workspace["owner"])) as conn,
        conn,
        conn.cursor() as cursor,
    ):
        cursor.execute("ALTER ROLE outreached_app REPLICATION")
    try:
        with pytest.raises(ValueError, match="privilèges excessifs"):
            migrate(postgres_workspace["owner"])
        with pytest.raises(WorkspaceStoreError, match="restreint"):
            init_workspace()
    finally:
        with (
            closing(psycopg2.connect(postgres_workspace["owner"])) as conn,
            conn,
            conn.cursor() as cursor,
        ):
            cursor.execute("ALTER ROLE outreached_app NOREPLICATION")


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///tmp/workspace.db",
        "postgresql://owner:private@db.example.test/database?sslmode=disable",
        "postgresql://owner:private@db.example.test/database?sslmode=require",
        "postgresql://owner:private@db.example.test/database?options=-c%20search_path=public",
        "postgresql://owner:private@db.example.test/database?sslmode=verify-full&sslmode=disable",
        "postgresql://owner@db.example.test/database",
    ],
)
def test_database_configuration_rejects_unsafe_urls_without_echoing_secrets(url):
    with pytest.raises(RuntimeError) as error:
        validate_database_url(url, production=True)
    assert "private" not in str(error.value)


def test_production_pg_config_does_not_require_a_local_file_path():
    config = Settings(
        _env_file=None,
        app_env="production",
        debug=False,
        workspace_database_url="postgresql://outreached_app:private@db.example.test/workspace?sslmode=verify-full",
        workspace_db_path="",
        workspace_api_key="k" * 40,
        secret_key="s" * 40,
        allowed_hosts=["app.example.test"],
        allowed_origins=["https://app.example.test"],
    )
    config.validate_production()
    assert "private" not in repr(config)


def test_parameter_translation_keeps_quoted_sql_and_values_untouched():
    query = "SELECT ':literal?', \"question?\", ?::text, '50%' -- ? ignored\n/* :ignore */"
    assert postgres_query(query, ("value?%:name",)) == (
        "SELECT ':literal?', \"question?\", %s::text, '50%%' -- ? ignored\n/* :ignore */"
    )
    assert (
        postgres_query("SELECT :value, ':literal'", {"value": "?"})
        == "SELECT %(value)s, ':literal'"
    )
