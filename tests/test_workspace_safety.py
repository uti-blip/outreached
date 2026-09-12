"""Regression checks for durable state, input failures, and production startup."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlite3 import OperationalError
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.config import Settings, settings
from backend.app.main import create_app
from backend.app.prospecting import LeadInput, add_lead, build_sequence
from backend.app.workspace_store import connect, init_workspace


def production_settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "app_env": "production",
            "workspace_api_key": "k" * 40,
            "secret_key": "s" * 40,
            "allowed_hosts": ["api.example.com"],
            "allowed_origins": ["https://app.example.com"],
            "workspace_db_path": "/data/workspace.db",
            **overrides,
        },
    )


@pytest.mark.parametrize("environment", ["prod", "staging", "developmnt", ""])
def test_unknown_environment_cannot_disable_production_safety(environment):
    with pytest.raises(ValidationError, match="app_env"):
        production_settings(app_env=environment)


def test_environment_casing_and_whitespace_do_not_disable_production_safety():
    config = production_settings(app_env=" Production ", workspace_api_key="")
    assert config.app_env == "production"
    with pytest.raises(RuntimeError, match="WORKSPACE_API_KEY"):
        config.validate_production()


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"debug": True}, "DEBUG"),
        ({"workspace_db_path": "backend/workspace.db"}, "WORKSPACE_DB_PATH"),
        ({"workspace_db_path": ""}, "WORKSPACE_DB_PATH"),
        ({"secret_key": "short" + " " * 40}, "SECRET_KEY"),
        ({"workspace_api_key": "k" * 40 + "\n"}, "WORKSPACE_API_KEY"),
        ({"allowed_hosts": ["api.example.com", "localhost"]}, "ALLOWED_HOSTS"),
        ({"allowed_hosts": ["https://api.example.com"]}, "ALLOWED_HOSTS"),
        ({"allowed_hosts": [""]}, "ALLOWED_HOSTS"),
        ({"allowed_hosts": ["127.1.2.3"]}, "ALLOWED_HOSTS"),
        ({"allowed_origins": ["https://"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://app.example.com/path"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://app.example.com?key=value"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://user:pass@app.example.com"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://@app.example.com"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://app.example.com:99999"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://[::1]"]}, "ALLOWED_ORIGINS"),
    ],
)
def test_unsafe_production_configuration_fails_closed(overrides, error):
    with pytest.raises(RuntimeError, match=error):
        production_settings(**overrides).validate_production()


def test_concurrent_creations_cannot_duplicate_a_contact():
    init_workspace()
    lead = LeadInput(company_name="Same company", email="same@example.com")

    def create():
        with connect() as conn:
            return add_lead(conn, lead)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: create(), range(16)))

    assert sum(created for _, created in results) == 1
    assert len({lead_id for lead_id, _ in results}) == 1
    with connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_reader_has_consistent_snapshot_without_blocking_writes():
    init_workspace()

    def write_profile():
        with connect() as conn:
            conn.execute("INSERT INTO profile (id,data) VALUES (1,'{}')")

    with ThreadPoolExecutor(max_workers=1) as pool, connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0
        pool.submit(write_profile).result(timeout=2)
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0
        with pytest.raises(OperationalError, match="readonly"):
            conn.execute("DELETE FROM profile")

    with connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 1


def test_readiness_reports_missing_storage_without_creating_it(monkeypatch, tmp_path):
    with TestClient(create_app()) as client:
        assert client.get("/health/ready").json() == {"status": "ready"}
        missing = tmp_path / "unmounted" / "missing.db"
        monkeypatch.setattr(settings, "workspace_db_path", str(missing))
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert not missing.parent.exists()
        assert client.get("/health").status_code == 200


def test_read_only_storage_paths_encode_uri_characters(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_db_path", str(tmp_path / "workspace #1.db"))
    init_workspace()
    with connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 0
    assert Path(settings.workspace_db_path).exists()


@pytest.mark.parametrize(
    "csv_text",
    [
        "company_name," + "x" * 150_000 + "\nAcme,test",
        'company_name,notes\nAcme,"never closed',
    ],
    ids=["oversized_header", "unterminated_quoted_value"],
)
def test_malformed_csv_is_actionable_and_does_not_import(csv_text):
    with TestClient(create_app()) as client:
        response = client.post("/api/leads/import", json={"csv_text": csv_text})
        assert response.status_code == 422
        assert "CSV illisible" in response.json()["detail"]
        assert client.get("/api/workspace").json()["leads"] == []


@pytest.mark.parametrize("payload", [["wrong shape"], {"results": [None]}])
def test_malformed_directory_response_does_not_crash_the_api(payload):
    response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://recherche-entreprises.api.gouv.fr/search"),
    )
    with (
        TestClient(create_app()) as client,
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response),
    ):
        result = client.get("/api/companies/search?q=Example")
        assert result.status_code == 502
        assert "annuaire public" in result.json()["detail"]


def test_opt_out_mailto_encodes_recipient_query_characters():
    sender = "sales?inbox&team@example.com"
    sequence = build_sequence(
        {"company_name": "Example", "contact_name": "", "source": "Salon B2B"},
        {
            "company_name": "Acme",
            "sender_name": "Sam",
            "sender_email": sender,
            "signature": "",
            "offer": "Conseil",
            "call_to_action": "Disponible ?",
            "privacy_url": "",
        },
    )
    opt_out = sequence[0]["footer"].split("Opposition par email : ")[1]
    parsed = urlparse(opt_out)
    assert parsed.query == "subject=STOP"
    assert unquote(parsed.path) == sender


@pytest.mark.parametrize(
    "lead",
    [
        {},
        {"company_name": "   "},
        {"company_name": "Example", "domain": {"nested": "object"}},
        {"company_name": "Example", "email": "invalid"},
        {"company_name": "Example", "linkedin_url": ["not a URL"]},
        {"company_name": "Example", "domain": "x" * 254},
    ],
    ids=["missing_name", "blank_name", "nested_domain", "invalid_email", "list_url", "long_domain"],
)
def test_campaign_seed_is_validated_before_runner(lead):
    with (
        TestClient(create_app()) as client,
        patch("backend.app.main.run_campaign", new_callable=AsyncMock) as runner,
    ):
        response = client.post("/api/campaign/run", json={"seed_list": [lead]})
        assert response.status_code == 422
        runner.assert_not_called()
