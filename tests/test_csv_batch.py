"""CSV import preserves safety and stays bounded on an actual remote DB driver."""

import csv
import io
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from backend.app import prospecting
from backend.app.main import create_app
from backend.app.prospecting import ImportRequest, LeadInput, add_lead, import_csv
from backend.app.workspace_store import connect, insert_rows
from tests.test_workspace_postgres import postgres_workspace as postgres_workspace


@pytest.fixture(params=["sqlite", "postgres"])
def workspace(request):
    if request.param == "postgres":
        request.getfixturevalue("postgres_workspace")
    with TestClient(create_app()) as client:
        yield client


def csv_text(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[*prospecting.CSV_FIELDS, "qualified"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def test_batch_preserves_duplicates_suppressions_validation_and_literal_values(workspace):
    with connect() as conn:
        existing, _ = add_lead(
            conn, LeadInput(company_name="Existing", email="old@example.test", siren="123456789")
        )
        conn.execute(
            "INSERT INTO suppressions(email,created_at) VALUES (?,?)",
            ("blocked@example.test", "2026-09-12"),
        )
    literal = "Robert'); DROP TABLE leads;-- ? %s :named 100%"
    rows = [
        # A duplicate must not reserve the unused SIREN or email it carries.
        {"company_name": "Duplicate", "email": "OLD@example.test", "siren": "111111111"},
        {"company_name": "New siren", "siren": "111111111"},
        {"company_name": "Duplicate", "email": "free@example.test", "siren": "123456789"},
        {"company_name": "New email", "email": "free@example.test"},
        {"company_name": "First", "email": "first@example.test", "siren": "222222222"},
        {"company_name": "Same email", "email": "FIRST@example.test", "siren": "333333333"},
        {"company_name": "Same siren", "email": "available@example.test", "siren": "222222222"},
        {"company_name": "Both unused", "email": "available@example.test", "siren": "333333333"},
        {"company_name": "Without email", "city": "Paris"},
        {"company_name": "WITHOUT EMAIL", "city": "PARIS"},
        {"company_name": "Without email", "city": "Lyon"},
        {"company_name": literal, "notes": "Deux\nlignes ? :name 100%", "qualified": "true"},
        {"company_name": "Blocked", "email": "blocked@example.test"},
        {"company_name": "Bad", "email": "not-an-email"},
    ]
    result = workspace.post("/api/leads/import", json={"csv_text": csv_text(rows)})
    assert result.status_code == 200, result.text
    assert result.json()["created"] == 8
    assert result.json()["duplicates"] == 5
    assert result.json()["errors"][0]["row"] == 15
    with connect(write=False) as conn:
        leads = [dict(row) for row in conn.execute("SELECT * FROM leads").fetchall()]
        events = conn.execute("SELECT * FROM events").fetchall()
    assert len(leads) == len(events) == 9
    assert next(lead for lead in leads if lead["id"] == existing)["siren"] == "123456789"
    assert next(lead for lead in leads if lead["company_name"] == literal)["notes"] == (
        "Deux\nlignes ? :name 100%"
    )
    blocked = next(lead for lead in leads if lead["email"] == "blocked@example.test")
    assert blocked["status"] == "do_not_contact"
    assert all(not lead["qualified"] for lead in leads)


class CountingCursor:
    def __init__(self, cursor, statements):
        self.cursor, self.statements = cursor, statements

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        return self.cursor.execute(statement, parameters)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)


class CountingConnection:
    def __init__(self, connection, statements):
        self.connection, self.statements = connection, statements

    def cursor(self, *args, **kwargs):
        return CountingCursor(self.connection.cursor(*args, **kwargs), self.statements)


def test_thousand_row_import_has_bounded_database_requests(workspace, monkeypatch):
    statements = []
    is_postgres = False

    @contextmanager
    def counted_connect():
        nonlocal is_postgres
        with connect() as conn:
            is_postgres = getattr(conn, "is_postgres", False)
            if is_postgres:
                # Observe real psycopg2 cursor executions, including execute_values.
                conn.connection = CountingConnection(conn.connection, statements)
            else:
                conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(prospecting, "connect", counted_connect)
    rows = [
        {"company_name": f"Synthetic {index}", "email": f"lead-{index}@example.test"}
        for index in range(1000)
    ]
    result = import_csv(ImportRequest(csv_text=csv_text(rows)))
    assert result == {"created": 1000, "duplicates": 0, "errors": []}
    if is_postgres:
        assert len(statements) == 3  # One bounded lookup + two real bulk inserts.
    else:
        assert sum(str(statement).startswith("WITH incoming") for statement in statements) == 7
    with connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1000
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1000
    statements.clear()
    assert import_csv(ImportRequest(csv_text=csv_text(rows))) == {
        "created": 0,
        "duplicates": 1000,
        "errors": [],
    }
    if is_postgres:
        assert len(statements) == 1


def test_duplicates_across_sqlite_read_batches_are_not_reserved_twice(workspace):
    rows = [
        {"company_name": f"Company {index}", "siren": str(100000000 + index)}
        for index in range(900)
    ]
    rows.extend(
        [
            {"company_name": "Company 0"},
            {"company_name": "Another", "siren": "100000899"},
        ]
    )
    assert import_csv(ImportRequest(csv_text=csv_text(rows))) == {
        "created": 900,
        "duplicates": 2,
        "errors": [],
    }


def test_batch_uses_database_case_matching_for_unicode_company_names(workspace):
    with connect(write=False) as conn:
        equal = conn.execute("SELECT lower(?)=lower(?)", ("Équipe", "équipe")).fetchone()[0]
    result = import_csv(
        ImportRequest(csv_text=csv_text([{"company_name": "Équipe"}, {"company_name": "équipe"}]))
    )
    assert result == {"created": 2 - int(equal), "duplicates": int(equal), "errors": []}


def test_manual_batch_keeps_explicit_qualification(workspace):
    lead = {"company_name": "Verified manually", "email": "one@example.test", "qualified": True}
    response = workspace.post("/api/leads/batch", json={"leads": [lead, lead]})
    assert response.status_code == 200
    assert response.json() == {"created": 1, "duplicates": 1}
    with connect(write=False) as conn:
        assert conn.execute("SELECT qualified FROM leads").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_batch_is_atomic_if_event_write_fails(workspace, monkeypatch):
    original = prospecting.insert_rows

    def fail_event_write(conn, table, columns, rows):
        if table == "events":
            raise RuntimeError("synthetic event failure")
        return original(conn, table, columns, rows)

    monkeypatch.setattr(prospecting, "insert_rows", fail_event_write)
    with pytest.raises(RuntimeError, match="synthetic event failure"):
        import_csv(ImportRequest(csv_text="company_name,email\nSynthetic,one@example.test\n"))
    with connect(write=False) as conn:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_insert_rows_rejects_untrusted_identifiers_before_querying():
    with pytest.raises(ValueError, match="identifiers"):
        insert_rows(None, "leads; DROP TABLE leads", ("id",), [("synthetic",)])
    with pytest.raises(ValueError, match="identifiers"):
        insert_rows(None, "leads", ('id") VALUES (1);--',), [("synthetic",)])
