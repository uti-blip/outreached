"""Large workspace views keep a bounded number of remote database round trips."""

import csv
import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import prospecting
from backend.app.main import create_app
from backend.app.prospecting import Profile
from backend.app.workspace_store import connect, init_workspace

CSV_COLUMNS = [
    "company_name",
    "siren",
    "contact_name",
    "email",
    "website",
    "city",
    "activity",
    "source",
    "notes",
    "status",
    "qualified",
    "created_at",
    "next_action_at",
]


def seed_workspace(size):
    init_workspace()
    created = datetime(2026, 8, 1, tzinfo=UTC)
    sent_dates = [
        "2026-09-01T10:00:00+00:00",
        "2026-09-05T10:00:00+00:00",
        "2026-09-09T10:00:00+00:00",
    ]
    expected_csv, summaries = [], {}
    with connect() as conn:
        profile = Profile(company_name="Workspace fixture").model_dump_json()
        conn.execute("INSERT INTO profile(id,data) VALUES(1,?)", (profile,))
        conn.execute(
            "INSERT INTO campaigns VALUES(?,?,?,?)",
            ("fixture", "Fixture", profile, created.isoformat()),
        )
        for index in range(size):
            case = index % 6
            identifier = f"lead-{index:04}"
            timestamp = (created + timedelta(seconds=index)).isoformat()
            status = "replied" if case == 3 else "contacted" if case in (1, 2) else "new"
            qualified = case != 4
            company = "=SUM(1,2)" if index == 0 else f"Company {index:04}"
            note = 'Contact rencontré,\navec une note "citée".'
            email = f"lead-{index}@example.test"
            conn.execute(
                "INSERT INTO leads(id,company_name,contact_name,email,website,city,activity,source,notes,status,qualified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    company,
                    "Camille",
                    email,
                    "https://example.test",
                    "Paris",
                    "Conseil",
                    "Salon B2B",
                    note,
                    status,
                    qualified,
                    timestamp,
                    timestamp,
                ),
            )
            sent_count = 3 if case == 2 else 1 if case in (1, 3) else 0
            draft_count = 0 if case == 5 else 3
            # Insert steps backwards: next-action calculation must use step order.
            for step in reversed(range(1, draft_count + 1)):
                conn.execute(
                    "INSERT INTO drafts(id,campaign_id,lead_id,step,subject,body,footer,sent_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"draft-{index}-{step}",
                        "fixture",
                        identifier,
                        step,
                        "Subject",
                        "Body",
                        "Footer",
                        sent_dates[step - 1] if step <= sent_count else None,
                    ),
                )
            next_action = (
                timestamp if case == 0 else "2026-09-05T10:00:00+00:00" if case == 1 else None
            )
            expected_csv.append(
                {
                    "company_name": "'=SUM(1,2)" if index == 0 else company,
                    "siren": "",
                    "contact_name": "Camille",
                    "email": email,
                    "website": "https://example.test",
                    "city": "Paris",
                    "activity": "Conseil",
                    "source": "Salon B2B",
                    "notes": note,
                    "status": status,
                    "qualified": "True" if qualified else "",
                    "created_at": timestamp,
                    "next_action_at": next_action or "",
                }
            )
            summaries[identifier] = {
                "draft_count": draft_count,
                "sent_count": sent_count,
                "next_action_at": next_action,
                "ready": case not in (3, 4),
                "qualified": qualified,
            }
    return expected_csv, summaries, json.loads(profile)


@pytest.mark.parametrize("size", [6, 600])
def test_csv_and_workspace_read_queries_are_bounded_and_fields_are_preserved(size, monkeypatch):
    expected_csv, summaries, profile = seed_workspace(size)
    statements = []

    @contextmanager
    def counted_connect(*, write=True):
        assert write is False, "Listing and export must use a consistent read-only snapshot"
        with connect(write=False) as conn:
            conn.set_trace_callback(
                lambda statement: (
                    statements.append(statement)
                    if statement.lstrip().upper().startswith("SELECT")
                    else None
                )
            )
            yield conn

    monkeypatch.setattr(prospecting, "connect", counted_connect)
    with TestClient(create_app()) as client:
        exported = client.get("/api/export/leads.csv")
        assert exported.status_code == 200
        assert len(statements) <= 2, (
            f"CSV export issued {len(statements)} reads for {size} prospects"
        )
        assert exported.content.startswith(b"\xef\xbb\xbf")
        assert (
            exported.headers["content-disposition"] == 'attachment; filename="lexia-prospects.csv"'
        )
        reader = csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff")))
        assert reader.fieldnames == CSV_COLUMNS
        assert list(reader) == expected_csv

        statements.clear()
        response = client.get("/api/workspace")
        assert response.status_code == 200
        assert len(statements) <= 4, (
            f"Workspace issued {len(statements)} reads for {size} prospects"
        )
        workspace = response.json()
        assert workspace["profile"] == profile
        assert [lead["id"] for lead in workspace["leads"]] == list(reversed(summaries))
        for lead in workspace["leads"]:
            assert {key: lead[key] for key in summaries[lead["id"]]} == summaries[lead["id"]]
        assert workspace["campaigns"] == [
            {
                "id": "fixture",
                "name": "Fixture",
                "created_at": "2026-08-01T00:00:00+00:00",
                "leads": size // 6 * 5,
                "drafts": size // 6 * 15,
                "sent": size // 6 * 5,
            }
        ]
