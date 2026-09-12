"""The deployed smoke probe is authenticated, bounded, and changes no CRM data."""

import json

import httpx
import pytest

from scripts import smoke_deployment as smoke

FRONTEND = "https://workspace.example.test"
BACKEND = "https://api.example.test"
USERNAME = "smoke-owner"
PASSWORD = "private-login-password"
API_KEY = "private-backend-key"
COOKIE = "private-session-value"
CSRF = "c" * 43
WORKSPACE = {"profile": {}, "leads": [], "campaigns": []}


class Deployment:
    def __init__(self, *, csrf_status=403, clear_cookie=True, campaign_location="/login"):
        self.requests = []
        self.csrf_status = csrf_status
        self.clear_cookie = clear_cookie
        self.campaign_location = campaign_location
        self.clients = []

    def client_factory(self, **options):
        assert options["follow_redirects"] is False
        assert options["trust_env"] is False
        self.clients.append(options["base_url"])
        return httpx.Client(**options, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "api.example.test":
            assert "cookie" not in request.headers
            assert PASSWORD not in request.content.decode()
            if path in {"/health", "/health/ready"}:
                return httpx.Response(
                    200, json={"status": "ready" if path.endswith("ready") else "ok"}
                )
            assert request.method == "GET" and path == "/api/workspace"
            authorized = request.headers.get("authorization") == f"Bearer {API_KEY}"
            return httpx.Response(200 if authorized else 401, json=WORKSPACE if authorized else {})

        assert request.url.host == "workspace.example.test"
        assert "authorization" not in request.headers
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/campaign":
            return httpx.Response(307, headers={"Location": self.campaign_location})
        authenticated = request.headers.get("cookie") == f"__Host-workspace-session={COOKIE}"
        if path == "/api/workspace":
            return httpx.Response(
                200 if authenticated else 401, json=WORKSPACE if authenticated else {}
            )
        if path == "/api/auth/login":
            assert request.method == "POST"
            assert request.headers["origin"] == FRONTEND
            assert json.loads(request.content) == {"username": USERNAME, "password": PASSWORD}
            return httpx.Response(
                200,
                json={"ok": True},
                headers={
                    "Set-Cookie": f"__Host-workspace-session={COOKIE}; Path=/; HttpOnly; Secure; SameSite=Strict"
                },
            )
        assert authenticated
        if path == "/api/auth/session":
            return httpx.Response(200, json={"csrfToken": CSRF})
        if path == "/api/campaigns":
            assert request.method == "POST"
            assert request.headers["origin"] == FRONTEND
            assert "x-csrf-token" not in request.headers
            assert json.loads(request.content) == {}
            return httpx.Response(self.csrf_status, json={"detail": "blocked"})
        assert path == "/api/auth/logout" and request.method == "POST"
        assert request.headers["origin"] == FRONTEND
        assert request.headers["x-csrf-token"] == CSRF
        headers = (
            {
                "Set-Cookie": "__Host-workspace-session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0"
            }
            if self.clear_cookie
            else {}
        )
        return httpx.Response(200, json={"ok": True}, headers=headers)

    def run(self, **overrides):
        return smoke.verify_deployment(
            FRONTEND,
            BACKEND,
            **{
                "username": USERNAME,
                "password": PASSWORD,
                "workspace_api_key": API_KEY,
                "client_factory": self.client_factory,
                **overrides,
            },
        )


def test_complete_probe_only_posts_auth_and_invalid_business_input():
    deployment = Deployment()
    assert len(deployment.run()) == 7
    assert deployment.clients == [BACKEND, FRONTEND]
    posts = [request for request in deployment.requests if request.method == "POST"]
    assert [request.url.path for request in posts] == [
        "/api/auth/login",
        "/api/campaigns",
        "/api/auth/logout",
    ]
    assert all(request.method in {"GET", "POST"} for request in deployment.requests)
    assert "cookie" not in deployment.requests[-1].headers
    assert deployment.requests[-1].url.path == "/api/workspace"


def test_backend_key_is_optional_but_frontend_authentication_is_not():
    deployment = Deployment()
    assert len(deployment.run(workspace_api_key=None)) == 6
    assert any(request.url.path == "/api/auth/login" for request in deployment.requests)
    assert all("authorization" not in request.headers for request in deployment.requests)


@pytest.mark.parametrize("missing", [{"username": None}, {"password": ""}])
def test_missing_login_credentials_fail_before_network(missing):
    deployment = Deployment()
    with pytest.raises(smoke.SmokeError, match="WORKSPACE_LOGIN_PASSWORD"):
        deployment.run(**missing)
    assert deployment.clients == deployment.requests == []


@pytest.mark.parametrize(
    "url,allow_local",
    [
        ("http://workspace.example.test", False),
        ("http://workspace.example.test", True),
        ("http://127.0.0.1:3000", False),
        ("https://user:private@workspace.example.test", False),
        ("https://workspace.example.test/api", False),
        ("https://workspace.example.test?secret=value", False),
        ("https://workspace.example.test:99999", False),
    ],
)
def test_unsafe_origins_are_rejected_without_network_or_echoed_secrets(url, allow_local):
    deployment = Deployment()
    with pytest.raises(smoke.SmokeError) as error:
        smoke.verify_deployment(
            url,
            BACKEND,
            username=USERNAME,
            password=PASSWORD,
            allow_local=allow_local,
            client_factory=deployment.client_factory,
        )
    assert url not in str(error.value)
    assert "private" not in str(error.value)
    assert deployment.clients == deployment.requests == []


@pytest.mark.parametrize(
    "origin", ["http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000"]
)
def test_allow_local_only_permits_loopback_http(origin):
    assert smoke.validate_origin(origin, "Frontend", allow_local=True) == origin


def test_cross_origin_redirect_is_not_followed():
    deployment = Deployment(campaign_location="https://attacker.example/login")
    with pytest.raises(smoke.SmokeError, match="redirection"):
        deployment.run()
    assert all(request.url.host != "attacker.example" for request in deployment.requests)
    assert not any(request.method == "POST" for request in deployment.requests)


@pytest.mark.parametrize("status", [200, 401, 422, 500])
def test_broken_csrf_gate_is_failure_even_if_schema_rejects_input(status):
    deployment = Deployment(csrf_status=status)
    with pytest.raises(smoke.SmokeError, match="Protection CSRF"):
        deployment.run()
    probe = deployment.requests[-1]
    assert probe.url.path == "/api/campaigns" and json.loads(probe.content) == {}


def test_logout_must_remove_the_client_cookie():
    with pytest.raises(smoke.SmokeError, match="cookie de session encore présent"):
        Deployment(clear_cookie=False).run()


def test_network_errors_do_not_echo_request_or_credentials():
    def broken_factory(**options):
        def fail(request):
            raise httpx.ConnectError(f"Unexpected error containing {API_KEY}", request=request)

        return httpx.Client(**options, transport=httpx.MockTransport(fail))

    with pytest.raises(smoke.SmokeError) as error:
        Deployment().run(client_factory=broken_factory)
    assert API_KEY not in str(error.value)


def test_cli_credentials_come_from_environment_and_output_contains_no_secrets(monkeypatch, capsys):
    deployment = Deployment()
    original = smoke.verify_deployment
    monkeypatch.setenv("WORKSPACE_LOGIN_USER", USERNAME)
    monkeypatch.setenv("WORKSPACE_LOGIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("WORKSPACE_API_KEY", API_KEY)
    monkeypatch.setattr(
        smoke,
        "verify_deployment",
        lambda *args, **kwargs: original(*args, **kwargs, client_factory=deployment.client_factory),
    )
    assert smoke.main(["--frontend-url", FRONTEND, "--backend-url", BACKEND]) == 0
    output = capsys.readouterr()
    assert "Vérification complète réussie" in output.out
    assert not output.err
    for secret in [USERNAME, PASSWORD, API_KEY, COOKIE, CSRF]:
        assert secret not in output.out


def test_cli_does_not_report_success_when_credentials_are_missing(monkeypatch, capsys):
    monkeypatch.delenv("WORKSPACE_LOGIN_PASSWORD", raising=False)
    assert smoke.main(["--frontend-url", FRONTEND, "--backend-url", BACKEND]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "WORKSPACE_LOGIN_PASSWORD" in output.err
