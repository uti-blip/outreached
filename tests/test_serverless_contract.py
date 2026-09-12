"""Run the established business contract against the real Next.js HTTP server.

Only an explicitly configured, disposable loopback PostgreSQL database and
loopback HTTP server are accepted. These tests never target a cloud workspace.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.cookies import SimpleCookie
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import psycopg2
import pytest

from backend.app.config import settings
from tests import test_prospecting as contract

CASES = [
    (contract.test_workspace_is_real_empty_and_persists_across_app_restart, {}),
    (contract.test_contact_validation_and_email_normalization, {}),
    (contract.test_deduplication_by_email_siren_and_company_city, {}),
    (contract.test_csv_bom_semicolon_multiline_partial_errors_and_duplicates, {}),
    (contract.test_csv_invalid_headers_or_too_many_rows_leave_db_unchanged, {}),
    (contract.test_export_is_safe_for_spreadsheets_and_backup_has_every_table, {}),
    (contract.test_campaign_requires_profile_and_qualified_contacts, {}),
    (contract.test_full_workflow_preparation_is_not_send_and_sequence_is_not_duplicated, {}),
    (contract.test_relances_enforce_order_and_delay, {}),
    (contract.test_suppression_survives_email_edit_and_reimport, {}),
    (contract.test_editing_drafts_preserves_footer_and_sent_history, {}),
    *[
        (contract.test_stop_status_blocks_contact_and_followups, {"status": status})
        for status in ("replied", "meeting", "won", "lost", "do_not_contact")
    ],
]


@pytest.fixture
def serverless_client(monkeypatch):
    address = os.environ.get("WORKSPACE_SERVERLESS_TEST_URL")
    if not address:
        pytest.skip("WORKSPACE_SERVERLESS_TEST_URL requires an isolated Next.js server")
    owner = os.environ["WORKSPACE_TEST_POSTGRES_URL"]
    runtime_password = os.environ["WORKSPACE_TEST_RUNTIME_PASSWORD"]
    parsed, database = urlsplit(address), urlsplit(owner)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or database.hostname not in {"127.0.0.1", "localhost", "::1"}
        or database.path != "/outreached_test"
    ):
        pytest.fail("Contract tests require loopback HTTP and /outreached_test PostgreSQL")
    root_cert = os.environ.get("WORKSPACE_TEST_POSTGRES_ROOT_CERT", "")
    options = {"sslrootcert": root_cert} if root_cert else {}
    with (
        closing(psycopg2.connect(owner, **options)) as connection,
        connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "TRUNCATE outreached_workspace.profile, outreached_workspace.leads, "
            "outreached_workspace.campaigns, outreached_workspace.drafts, "
            "outreached_workspace.events, outreached_workspace.suppressions "
            "RESTART IDENTITY CASCADE"
        )
        cursor.execute(
            "UPDATE outreached_workspace.login_rate_limit SET state='{}'::jsonb WHERE id=1"
        )
    runtime = urlunsplit(
        (
            database.scheme,
            f"outreached_app:{quote(runtime_password, safe='')}@127.0.0.1:{database.port}",
            database.path,
            database.query,
            "",
        )
    )
    monkeypatch.setattr(settings, "workspace_database_url", runtime)
    monkeypatch.setattr(settings, "workspace_database_ssl_root_cert", root_cert)
    with httpx.Client(
        base_url=address,
        timeout=30,
        headers={
            "Host": "workspace.example.test",
            "X-Forwarded-Proto": "https",
            "Origin": "https://workspace.example.test",
        },
    ) as client:
        response = client.post(
            "/api/auth/login",
            json={
                "username": os.environ["WORKSPACE_LOGIN_USER"],
                "password": os.environ["WORKSPACE_LOGIN_PASSWORD"],
            },
        )
        assert response.status_code == 200, response.text
        cookie = SimpleCookie(response.headers["set-cookie"])["__Host-workspace-session"]
        assert cookie["secure"] and cookie["httponly"]
        # HTTP loopback emulates the HTTPS ingress. Keep Secure in the app cookie.
        client.headers["Cookie"] = f"__Host-workspace-session={cookie.value}"
        session = client.get("/api/auth/session")
        assert session.status_code == 200
        client.headers["X-CSRF-Token"] = session.json()["csrfToken"]
        yield client


@pytest.mark.parametrize(
    "check,arguments",
    CASES,
    ids=[check.__name__ + ("-" + values["status"] if values else "") for check, values in CASES],
)
def test_existing_business_contract(serverless_client, check, arguments):
    check(serverless_client, **arguments)


def test_concurrent_http_creations_remain_unique(serverless_client):
    data = {"company_name": "Concurrent fixture", "email": "parallel@example.test"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(lambda _: serverless_client.post("/api/leads", json=data), range(16))
        )
    assert all(response.status_code == 201 for response in responses)
    values = [response.json() for response in responses]
    assert sum(value["created"] for value in values) == 1
    assert len({value["lead"]["id"] for value in values}) == 1


def test_thousand_row_csv_and_reimport(serverless_client):
    text = "company_name,email,source\n" + "".join(
        f"Fixture {index},fixture{index}@example.test,Synthetic contract\n" for index in range(1000)
    )
    first = serverless_client.post("/api/leads/import", json={"csv_text": text})
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 1000
    repeated = serverless_client.post("/api/leads/import", json={"csv_text": text})
    assert repeated.status_code == 200
    assert repeated.json()["created"] == 0 and repeated.json()["duplicates"] == 1000
    workspace = serverless_client.get("/api/workspace").json()
    assert all(lead["qualified"] is False for lead in workspace["leads"])
