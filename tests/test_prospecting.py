"""Exercise the persisted prospecting API without paid services or email sends."""

import csv
import io
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.main import create_app
from backend.app.workspace_store import connect


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def add(client, **overrides):
    response = client.post(
        "/api/leads",
        json={
            "company_name": "Entreprise Test",
            "email": "hello@example.com",
            "contact_name": "Camille",
            "source": "Rencontre professionnelle",
            "qualified": True,
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["lead"]


def profile(client):
    response = client.put(
        "/api/profile",
        json={
            "sender_name": "Enzo",
            "sender_email": "enzo@example.org",
            "company_name": "Lexia",
            "offer": "Nous aidons les équipes à organiser leur acquisition.",
            "target": "Équipes commerciales B2B",
            "signature": "Lexia, Paris",
        },
    )
    assert response.status_code == 200


def sequence(client, lead):
    profile(client)
    response = client.post(
        "/api/campaigns", json={"name": "Campagne test", "lead_ids": [lead["id"]]}
    )
    assert response.status_code == 201, response.text
    return client.get(f"/api/leads/{lead['id']}/detail").json()["drafts"]


def update(client, lead, **overrides):
    fields = {
        key: lead[key]
        for key in (
            "company_name",
            "siren",
            "contact_name",
            "email",
            "website",
            "city",
            "activity",
            "source",
            "notes",
            "qualified",
            "status",
        )
    }
    return client.put(f"/api/leads/{lead['id']}", json={**fields, **overrides})


def test_workspace_is_real_empty_and_persists_across_app_restart(client):
    assert client.get("/api/workspace").json()["leads"] == []
    lead = add(client)
    with TestClient(create_app()) as restarted:
        data = restarted.get("/api/workspace").json()
    assert data["leads"][0]["id"] == lead["id"]
    assert data["mode"] == "local_manual"
    assert data["provider_cost_eur"] == 0


def test_contact_validation_and_email_normalization(client):
    lead = add(client, email=" HELLO@EXAMPLE.COM ")
    assert lead["email"] == "hello@example.com"
    for bad in [
        "bad",
        "x@example.com\r\nBcc:other@example.com",
        "x@example.com?cc=other@example.com",
    ]:
        assert (
            client.post("/api/leads", json={"company_name": "A", "email": bad}).status_code == 422
        )
    assert (
        client.post(
            "/api/leads", json={"company_name": "A", "website": "javascript:alert(1)"}
        ).status_code
        == 422
    )
    assert client.post("/api/leads", json={"company_name": ""}).status_code == 422


def test_deduplication_by_email_siren_and_company_city(client):
    first = add(client, siren="123456789")
    for data in [
        {"company_name": "Different", "email": "HELLO@EXAMPLE.COM"},
        {"company_name": "Different", "siren": "123456789"},
    ]:
        result = client.post("/api/leads", json=data).json()
        assert result["created"] is False
        assert result["lead"]["id"] == first["id"]
    one = add(client, email="", company_name="Sans email", city="Paris")
    assert add(client, email="", company_name="Sans email", city="Paris")["id"] == one["id"]
    other = add(client, email="other@example.com", company_name="Other")
    assert update(client, other, email=first["email"]).status_code == 409


def test_csv_bom_semicolon_multiline_partial_errors_and_duplicates(client):
    text = '\ufeffentreprise;email;ville;source;notes\n"Société; Test";ONE@example.com;Paris;Salon;"Deux\nlignes"\nDuplicate;one@example.com;;;\nInvalide;pas-un-email;;;\n'
    result = client.post("/api/leads/import", json={"csv_text": text})
    assert result.status_code == 200, result.text
    assert result.json()["created"] == 1
    assert result.json()["duplicates"] == 1
    assert len(result.json()["errors"]) == 1
    lead = client.get("/api/workspace").json()["leads"][0]
    assert lead["company_name"] == "Société; Test"
    assert lead["notes"] == "Deux\nlignes"
    assert lead["qualified"] is False
    assert lead["ready"] is False


def test_csv_invalid_headers_or_too_many_rows_leave_db_unchanged(client):
    for text in [
        "email\na@example.com",
        "company_name,entreprise\nOne,Two",
        "company_name\n" + "Acme\n" * 1001,
    ]:
        assert client.post("/api/leads/import", json={"csv_text": text}).status_code == 422
    assert client.get("/api/workspace").json()["leads"] == []


def test_export_is_safe_for_spreadsheets_and_backup_has_every_table(client):
    add(client, company_name="=HYPERLINK(bad)", notes="  +FORMULA")
    response = client.get("/api/export/leads.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    row = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))[0]
    assert row["company_name"].startswith("'=")
    assert row["notes"].startswith("'+")
    backup = client.get("/api/export/backup.json").json()
    assert set(backup["data"]) == {
        "profile",
        "leads",
        "suppressions",
        "campaigns",
        "drafts",
        "events",
    }


def test_campaign_requires_profile_and_qualified_contacts(client):
    lead = add(client, qualified=False)
    request = {"name": "Test", "lead_ids": [lead["id"]]}
    assert client.post("/api/campaigns", json=request).status_code == 422
    profile(client)
    result = client.post("/api/campaigns", json=request)
    assert result.status_code == 422
    assert "skipped" in result.json()["detail"]
    assert client.get("/api/workspace").json()["campaigns"] == []


def test_full_workflow_preparation_is_not_send_and_sequence_is_not_duplicated(client):
    lead = add(client)
    drafts = sequence(client, lead)
    assert len(drafts) == 3
    assert "STOP" in drafts[0]["footer"] and "Lexia" in drafts[0]["footer"]
    assert "mailto:enzo@example.org?subject=STOP" in drafts[0]["footer"]
    assert "Camille" in drafts[0]["body"]
    prepared = client.post(f"/api/drafts/{drafts[0]['id']}/prepare").json()
    parsed = urlparse(prepared["mailto"])
    assert parsed.scheme == "mailto" and parsed.path == "hello@example.com"
    assert parse_qs(parsed.query)["body"][0] == prepared["body"]
    assert client.get("/api/workspace").json()["leads"][0]["sent_count"] == 0
    sent = client.post(f"/api/drafts/{drafts[0]['id']}/mark-sent")
    assert sent.status_code == 200 and sent.json()["delivery_verified"] is False
    assert client.post(f"/api/drafts/{drafts[0]['id']}/mark-sent").status_code == 409
    assert client.post(f"/api/drafts/{drafts[0]['id']}/prepare").status_code == 409
    assert (
        client.post(
            "/api/campaigns", json={"name": "Duplicate", "lead_ids": [lead["id"]]}
        ).status_code
        == 422
    )
    persisted = client.get("/api/workspace").json()["leads"][0]
    assert persisted["status"] == "contacted" and persisted["sent_count"] == 1
    assert datetime.fromisoformat(persisted["next_action_at"]) > datetime.now(UTC)


def test_relances_enforce_order_and_delay(client):
    lead = add(client)
    drafts = sequence(client, lead)
    second = f"/api/drafts/{drafts[1]['id']}"
    assert client.post(second + "/mark-sent").status_code == 409
    assert client.post(second + "/prepare").status_code == 409
    assert client.post(f"/api/drafts/{drafts[0]['id']}/mark-sent").status_code == 200
    assert client.post(second + "/prepare").status_code == 409
    with connect() as conn:
        conn.execute(
            "UPDATE drafts SET sent_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(days=5)).isoformat(), drafts[0]["id"]),
        )
    assert client.post(second + "/prepare").status_code == 200
    assert client.post(second + "/mark-sent").status_code == 200
    assert client.post(f"/api/drafts/{drafts[2]['id']}/prepare").status_code == 409


@pytest.mark.parametrize("status", ["replied", "meeting", "won", "lost", "do_not_contact"])
def test_stop_status_blocks_contact_and_followups(client, status):
    lead = add(client)
    drafts = sequence(client, lead)
    assert update(client, lead, status=status).status_code == 200
    assert client.post(f"/api/drafts/{drafts[0]['id']}/prepare").status_code == 409
    assert client.post(f"/api/drafts/{drafts[0]['id']}/mark-sent").status_code == 409
    assert client.get("/api/workspace").json()["leads"][0]["next_action_at"] is None


def test_suppression_survives_email_edit_and_reimport(client):
    lead = add(client)
    assert update(client, lead, status="do_not_contact").status_code == 200
    assert update(client, lead, status="new").status_code == 409
    assert (
        update(client, lead, status="do_not_contact", email="replacement@example.com").status_code
        == 200
    )
    imported = add(client, company_name="Reimport", email="hello@example.com")
    assert imported["status"] == "do_not_contact"
    assert imported["ready"] is False


def test_editing_drafts_preserves_footer_and_sent_history(client):
    lead = add(client)
    drafts = sequence(client, lead)
    path = f"/api/drafts/{drafts[0]['id']}"
    assert (
        client.put(
            path, json={"subject": "Objet personnel", "body": "Bonjour, voici mon offre."}
        ).status_code
        == 200
    )
    assert (
        client.put(path, json={"subject": "Injected\r\nBcc:test", "body": "Message"}).status_code
        == 422
    )
    assert (
        client.put(path, json={"subject": "Objet", "body": "Message", "footer": ""}).status_code
        == 422
    )
    prepared = client.post(path + "/prepare").json()
    assert prepared["subject"] == "Objet personnel" and "STOP" in prepared["body"]
    client.post(path + "/mark-sent")
    assert client.put(path, json={"subject": "Autre", "body": "Changed"}).status_code == 409


def test_public_api_maps_real_company_fields_without_guessing_contacts(client):
    payload = {
        "results": [
            {
                "siren": "123456789",
                "nom_complet": "Société ouverte",
                "etat_administratif": "A",
                "statut_diffusion": "O",
                "activite_principale": "62.01Z",
                "siege": {"libelle_commune": "LYON"},
                "dirigeants": [{"nom": "Privé", "date_de_naissance": "1980"}],
            },
            {"siren": "111111111", "etat_administratif": "A", "statut_diffusion": "P"},
            {"siren": "222222222", "etat_administratif": "C", "statut_diffusion": "O"},
        ],
        "total_results": 3,
        "total_pages": 1,
    }
    response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://recherche-entreprises.api.gouv.fr/search"),
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response) as mock:
        result = client.get("/api/companies/search?activity=62.01Z&department=69")
    assert result.status_code == 200
    rows = result.json()["results"]
    assert len(rows) == 1 and rows[0]["city"] == "LYON"
    assert rows[0]["email"] == rows[0]["contact_name"] == rows[0]["website"] == ""
    assert "dirigeants" not in rows[0]
    assert mock.call_args.kwargs["params"]["departement"] == "69"
    assert client.get("/api/workspace").json()["leads"] == []


@pytest.mark.parametrize("code,expected", [(429, 429), (500, 502)])
def test_annuaire_failure_is_actionable(client, code, expected):
    response = httpx.Response(
        code, request=httpx.Request("GET", "https://recherche-entreprises.api.gouv.fr/search")
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response):
        result = client.get("/api/companies/search?q=Lexia")
    assert result.status_code == expected
    assert "detail" in result.json()


def test_request_validation_origin_host_auth_and_size_limits(client, monkeypatch):
    assert client.get("/api/companies/search").status_code == 422
    assert client.get("/api/companies/search?q=a&page=100").status_code == 422
    assert (
        client.get("/api/workspace", headers={"origin": "https://evil.example"}).status_code == 403
    )
    assert client.get("/api/workspace", headers={"host": "evil.example"}).status_code == 400
    monkeypatch.setattr(settings, "workspace_api_key", "a" * 32)
    assert client.get("/api/workspace").status_code == 401
    assert (
        client.get("/api/workspace", headers={"authorization": "Bearer wrong"}).status_code == 401
    )
    headers = {"authorization": "Bearer " + "a" * 32}
    assert client.get("/api/workspace", headers=headers).status_code == 200
    assert client.post("/api/leads", content="x" * 1_500_001, headers=headers).status_code == 413
    assert client.get("/health").status_code == 200


def test_production_refuses_unprotected_startup(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(RuntimeError, match="WORKSPACE_API_KEY"), TestClient(create_app()):
        pass


def test_demo_endpoint_is_explicitly_simulated_and_bounded(client):
    assert client.post("/api/campaign/run", json={"seed_list": []}).status_code == 422
    result = client.post(
        "/api/campaign/run", json={"seed_list": [{"company_name": "Test", "domain": "example.com"}]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["simulated"] is True and result.json()["mode"] == "demo"
    assert client.get("/api/workspace").json()["leads"] == []
