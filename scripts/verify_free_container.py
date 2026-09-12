"""Exercise synthetic business data in the isolated CI container only.

Connects exclusively to loopback HTTP while emulating an HTTPS reverse proxy.
Credentials, session cookies and business data are never printed. This helper
deliberately refuses public deployment URLs because it creates synthetic data.
"""

import argparse
import hashlib
import http.client
import json
import os
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

PUBLIC_ORIGIN = "https://workspace.example.test"
SESSION_COOKIE = "__Host-workspace-session"
PROFILE = {
    "company_name": "CI Synthetic Company",
    "sender_name": "CI Synthetic Sender",
    "sender_email": "sender@example.test",
    "offer": "Nous préparons des diagnostics documentés.",
    "target": "PME de test",
    "signature": "Fixture CI synthétique, aucun envoi réel",
}
LEAD = {
    "company_name": "CI Synthetic Prospect",
    "contact_name": "CI Synthetic Contact",
    "email": "prospect@example.test",
    "source": "Fixture synthétique CI isolée",
    "qualified": True,
    "notes": "Validation de la persistance PostgreSQL après redémarrage",
}


class VerificationError(RuntimeError):
    """A deliberately data-free check failure."""


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        try:
            value = json.loads(self.body)
        except ValueError:
            raise VerificationError("Expected valid JSON") from None
        if not isinstance(value, dict):
            raise VerificationError("Expected a JSON object")
        return value


class WorkspaceClient:
    def __init__(self, address: str):
        parsed = urlsplit(address)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise VerificationError("Synthetic test target must be an HTTP loopback origin")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.cookie = ""
        self.csrf = ""

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        expected: int = 200,
        with_csrf: bool = True,
        overrides: dict[str, str] | None = None,
    ) -> Response:
        headers = {"Host": "workspace.example.test", "X-Forwarded-Proto": "https"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD"}:
            headers["Origin"] = PUBLIC_ORIGIN
        if self.cookie:
            headers["Cookie"] = f"{SESSION_COOKIE}={self.cookie}"
        if self.csrf and with_csrf:
            headers["X-CSRF-Token"] = self.csrf
        headers.update(overrides or {})
        connection = http.client.HTTPConnection(self.host, self.port, timeout=15)
        try:
            connection.request(
                method,
                path,
                body=json.dumps(body).encode() if body is not None else None,
                headers=headers,
            )
            incoming = connection.getresponse()
            response = Response(
                incoming.status,
                {name.lower(): value for name, value in incoming.getheaders()},
                incoming.read(),
            )
        except (OSError, http.client.HTTPException):
            raise VerificationError(f"{method} {path}: connection failed") from None
        finally:
            connection.close()
        if response.status != expected:
            raise VerificationError(f"{method} {path}: HTTP {response.status}, expected {expected}")
        return response

    def ready(self) -> None:
        for _attempt in range(60):
            try:
                response = self.request("GET", "/health")
                if response.json().get("status") == "ready":
                    return
            except VerificationError:
                pass
            time.sleep(2)
        raise VerificationError("Combined frontend and PostgreSQL backend did not become ready")

    def login(self) -> None:
        self.request("GET", "/api/workspace", expected=401)
        redirect = self.request("GET", "/campaign", expected=307)
        if urlsplit(redirect.headers.get("location", "")).path != "/login":
            raise VerificationError("Unauthenticated campaign page did not redirect to login")
        self.request(
            "POST",
            "/api/auth/login",
            {"username": "unused", "password": "unused"},
            expected=403,
            overrides={"Origin": "https://attacker.example.test"},
        )
        response = self.request(
            "POST",
            "/api/auth/login",
            {
                "username": os.environ["WORKSPACE_LOGIN_USER"],
                "password": os.environ["WORKSPACE_LOGIN_PASSWORD"],
            },
        )
        cookies = SimpleCookie(response.headers.get("set-cookie", ""))
        cookie = cookies.get(SESSION_COOKIE)
        if (
            not cookie
            or not cookie["secure"]
            or not cookie["httponly"]
            or cookie["samesite"].lower() != "strict"
            or cookie["path"] != "/"
            or cookie["domain"]
        ):
            raise VerificationError("Production cookie attributes are invalid")
        self.cookie = cookie.value
        self.csrf = self.request("GET", "/api/auth/session").json().get("csrfToken", "")
        if not isinstance(self.csrf, str) or len(self.csrf) != 43:
            raise VerificationError("CSRF token is missing")
        self.request("GET", "/api/workspace")
        self.request("POST", "/api/campaigns", {}, expected=403, with_csrf=False)
        self.request(
            "POST",
            "/api/campaigns",
            {},
            expected=403,
            overrides={"X-CSRF-Token": "invalid"},
        )
        self.request(
            "POST",
            "/api/campaigns",
            {},
            expected=403,
            overrides={"Origin": "https://attacker.example.test"},
        )

    def logout(self) -> None:
        response = self.request("POST", "/api/auth/logout", {})
        cookie = SimpleCookie(response.headers.get("set-cookie", "")).get(SESSION_COOKIE)
        if not cookie or cookie.value or cookie["max-age"] != "0":
            raise VerificationError("Logout did not expire the session cookie")
        self.cookie = self.csrf = ""
        self.request("GET", "/api/workspace", expected=401)


def snapshot_digest(data: dict) -> str:
    # SQL row order is unspecified. Compare the complete synthetic workspace
    # independently of physical row order after a database/container restart.
    canonical = {
        table: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
        for table, rows in data.items()
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def seed_workspace(client: WorkspaceClient) -> dict:
    workspace = client.request("GET", "/api/workspace").json()
    if workspace["leads"] or workspace["campaigns"]:
        raise VerificationError("Synthetic fixture requires a fresh, empty workspace")
    client.request("PUT", "/api/profile", PROFILE)
    created = client.request("POST", "/api/leads", LEAD, expected=201).json()
    if not created.get("created"):
        raise VerificationError("Synthetic prospect was not created")
    lead_id = created["lead"]["id"]
    duplicate = client.request("POST", "/api/leads", LEAD, expected=201).json()
    if duplicate.get("created") or duplicate["lead"]["id"] != lead_id:
        raise VerificationError("Prospect deduplication failed")
    campaign = client.request(
        "POST",
        "/api/campaigns",
        {"name": "CI Synthetic Campaign", "lead_ids": [lead_id]},
        expected=201,
    ).json()
    if campaign.get("drafts") != 3:
        raise VerificationError("Three draft steps were not created")
    detail = client.request("GET", f"/api/leads/{lead_id}/detail").json()
    first, following = detail["drafts"][:2]
    prepared = client.request("POST", f"/api/drafts/{first['id']}/prepare").json()
    if prepared.get("to") != LEAD["email"] or not prepared.get("mailto", "").startswith("mailto:"):
        raise VerificationError("Draft preparation failed")
    # Only a synthetic database declaration; this endpoint performs no send.
    declared = client.request("POST", f"/api/drafts/{first['id']}/mark-sent").json()
    if declared.get("delivery_verified") is not False:
        raise VerificationError("A declaration was falsely reported as verified delivery")
    client.request("POST", f"/api/drafts/{first['id']}/mark-sent", expected=409)
    client.request("POST", f"/api/drafts/{following['id']}/prepare", expected=409)
    client.request("PUT", f"/api/leads/{lead_id}", {**LEAD, "status": "do_not_contact"})
    client.request("POST", f"/api/drafts/{following['id']}/prepare", expected=409)
    client.request("PUT", f"/api/leads/{lead_id}", {**LEAD, "status": "new"}, expected=409)
    backup = client.request("GET", "/api/export/backup.json").json()["data"]
    if len(backup["leads"]) != 1 or len(backup["drafts"]) != 3 or not backup["suppressions"]:
        raise VerificationError("Synthetic export is incomplete")
    if LEAD["company_name"] not in client.request("GET", "/api/export/leads.csv").body.decode():
        raise VerificationError("CSV export is incomplete")
    return {
        "lead_id": lead_id,
        "following_draft_id": following["id"],
        "snapshot_digest": snapshot_digest(backup),
    }


def verify_persistence(client: WorkspaceClient, state: dict) -> None:
    backup = client.request("GET", "/api/export/backup.json").json()["data"]
    if snapshot_digest(backup) != state["snapshot_digest"]:
        raise VerificationError("Workspace changed or lost data across the container restart")
    workspace = client.request("GET", "/api/workspace").json()
    if workspace["profile"]["company_name"] != PROFILE["company_name"]:
        raise VerificationError("Profile was not persisted")
    lead = next((row for row in workspace["leads"] if row["id"] == state["lead_id"]), None)
    if not lead or lead["status"] != "do_not_contact":
        raise VerificationError("Suppression did not survive restart")
    detail = client.request("GET", f"/api/leads/{state['lead_id']}/detail").json()
    if len(detail["drafts"]) != 3 or not any(
        event["kind"] == "manual_send" for event in detail["events"]
    ):
        raise VerificationError("Drafts or manual audit event did not survive restart")
    client.request("POST", f"/api/drafts/{state['following_draft_id']}/prepare", expected=409)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="http://127.0.0.1:10000")
    parser.add_argument("--phase", choices=["before-restart", "after-restart"], required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        client = WorkspaceClient(args.address)
        client.ready()
        client.login()
        if args.phase == "before-restart":
            state = seed_workspace(client)
            args.state_file.write_text(json.dumps(state), encoding="utf-8")
        else:
            verify_persistence(client, json.loads(args.state_file.read_text(encoding="utf-8")))
        client.logout()
    except (VerificationError, OSError, KeyError, ValueError) as exc:
        # Unexpected schema/config errors must not leak response bodies or secrets.
        message = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
        print(f"FAIL {args.phase}: {message}")
        return 1
    print(
        f"PASS {args.phase}: authentication, CSRF and synthetic PostgreSQL workspace; real sends=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
