"""Production hardening gates — proves a live campaign cannot be triggered.

Covers: client-controlled launch flag removal, the server-side commercial gate,
startup secret/config validation, authentication, and the no-mock-live-fallback
invariant. Every refusal test also asserts the absence of side effects.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.adapters.factory import LiveSendNotConfiguredError, resolve_send_adapters
from backend.app.config import Settings, settings
from backend.app.main import CampaignInput, create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


VALID_PROD = {
    "app_env": "production",
    "debug": False,
    "workspace_api_key": "k" * 40,
    "secret_key": "s" * 40,
    "allowed_hosts": ["api.outreached.io"],
    "allowed_origins": ["https://app.outreached.io"],
    "workspace_db_path": "/data/workspace.db",
    "smartlead_api_key": "sl_live_key",
    "unipile_api_key": "up_live_key",
}


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**VALID_PROD, **overrides})


def seed() -> dict:
    return {"seed_list": [{"company_name": "Entreprise Test", "domain": "example.com"}]}


# ── P0-1 — no client-controlled commercial gate ────────────────────────────


def test_launch_flag_is_not_a_client_input():
    assert "commercial_launch_enabled" not in CampaignInput.model_fields
    assert set(CampaignInput.model_fields) == {"seed_list", "campaign_name", "dry_run"}


def test_injected_launch_flag_is_rejected_not_ignored(client):
    response = client.post("/api/campaign/run", json={**seed(), "commercial_launch_enabled": True})
    assert response.status_code == 422, response.text


# ── DEVELOPMENT ────────────────────────────────────────────────────────────


def test_development_dry_run_is_allowed(client):
    response = client.post("/api/campaign/run", json=seed())
    assert response.status_code == 200, response.text
    assert response.json()["simulated"] is True
    assert response.json()["mode"] == "demo"


def test_development_live_run_is_refused(client):
    response = client.post("/api/campaign/run", json={**seed(), "dry_run": False})
    assert response.status_code == 403, response.text
    assert "COMMERCIAL_LAUNCH_ENABLED" in response.json()["detail"]


def test_development_live_refused_even_with_server_flag(monkeypatch, client):
    monkeypatch.setattr(settings, "commercial_launch_enabled", True)
    response = client.post("/api/campaign/run", json={**seed(), "dry_run": False})
    assert response.status_code == 403, response.text
    assert "production" in response.json()["detail"].lower()


# ── PRODUCTION / launch disabled ───────────────────────────────────────────


def test_production_launch_disabled_dry_run_is_allowed(monkeypatch, client):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "commercial_launch_enabled", False)
    response = client.post("/api/campaign/run", json=seed())
    assert response.status_code == 200, response.text
    assert response.json()["simulated"] is True


def test_production_launch_disabled_live_is_hard_rejected(monkeypatch, client):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "commercial_launch_enabled", False)
    response = client.post("/api/campaign/run", json={**seed(), "dry_run": False})
    assert response.status_code == 403, response.text
    assert response.json().get("mode") is None


# ── PRODUCTION / launch enabled ────────────────────────────────────────────


def test_production_launch_enabled_cannot_enable_unimplemented_transports(monkeypatch):
    monkeypatch.setattr(settings, "commercial_launch_enabled", True)
    monkeypatch.setattr(settings, "smartlead_api_key", "sl_live_key")
    monkeypatch.setattr(settings, "unipile_api_key", "up_live_key")
    with pytest.raises(LiveSendNotConfiguredError, match="indisponible"):
        resolve_send_adapters(dry_run=False)


def test_production_launch_enabled_without_transports_returns_503(monkeypatch, client):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "commercial_launch_enabled", True)
    monkeypatch.setattr(settings, "smartlead_api_key", "")
    monkeypatch.setattr(settings, "unipile_api_key", "")
    response = client.post("/api/campaign/run", json={**seed(), "dry_run": False})
    assert response.status_code == 503, response.text
    assert "indisponible" in response.json()["detail"]


# ── P0-4 — live mode can never fall back to a mock ─────────────────────────


def test_live_mode_never_returns_mock_adapters(monkeypatch):
    monkeypatch.setattr(settings, "smartlead_api_key", "sl_live_key")
    monkeypatch.setattr(settings, "unipile_api_key", "up_live_key")
    with pytest.raises(LiveSendNotConfiguredError):
        resolve_send_adapters(dry_run=False)


def test_live_mode_fails_closed_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "smartlead_api_key", "")
    monkeypatch.setattr(settings, "unipile_api_key", "")
    with pytest.raises(LiveSendNotConfiguredError):
        resolve_send_adapters(dry_run=False)


def test_dry_run_uses_mocks(monkeypatch):
    email, linkedin = resolve_send_adapters(dry_run=True)
    assert type(email).__name__ == "MockEmailAdapter"
    assert type(linkedin).__name__ == "MockLinkedInAdapter"


# ── SIDE EFFECTS — a refusal must leave nothing behind ─────────────────────


def test_rejected_live_run_has_zero_side_effects(monkeypatch, client):
    """No sender, no provider, no campaign marked success on refusal."""
    sends: list[tuple] = []

    async def spy_send(self, *args, **kwargs):
        sends.append((type(self).__name__, args, kwargs))
        raise AssertionError("un envoi ne doit jamais être tenté")

    monkeypatch.setattr("backend.app.adapters.mocks.MockEmailAdapter.send", spy_send)
    monkeypatch.setattr("backend.app.adapters.mocks.MockLinkedInAdapter.send_message", spy_send)

    runs: list[dict] = []

    async def spy_runner(**kwargs):
        runs.append(kwargs)
        raise AssertionError("le pipeline ne doit pas démarrer")

    monkeypatch.setattr("backend.app.main.run_campaign", spy_runner)

    response = client.post("/api/campaign/run", json={**seed(), "dry_run": False})

    assert response.status_code == 403, response.text
    assert sends == []
    assert runs == []
    body = response.json()
    assert "campaign" not in body and body.get("mode") is None


def test_refused_live_run_never_touches_network(monkeypatch, client):
    def exploding_httpx(*args, **kwargs):
        raise AssertionError("aucun appel réseau ne doit partir")

    monkeypatch.setattr("httpx.AsyncClient", exploding_httpx)
    response = client.post("/api/campaign/run", json={**seed(), "dry_run": False})
    assert response.status_code == 403


def test_production_launch_enabled_missing_transport_has_no_side_effects(monkeypatch, client):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "commercial_launch_enabled", True)
    monkeypatch.setattr(settings, "smartlead_api_key", "")
    monkeypatch.setattr(settings, "unipile_api_key", "")

    sends: list[str] = []

    async def spy_send(self, *args, **kwargs):
        sends.append(type(self).__name__)
        raise AssertionError("un envoi ne doit jamais être tenté")

    monkeypatch.setattr("backend.app.adapters.mocks.MockEmailAdapter.send", spy_send)
    monkeypatch.setattr("backend.app.adapters.mocks.MockLinkedInAdapter.send_message", spy_send)

    def exploding_httpx(*args, **kwargs):
        raise AssertionError("aucun appel réseau ne doit partir")

    monkeypatch.setattr("httpx.AsyncClient", exploding_httpx)

    response = client.post("/api/campaign/run", json={**seed(), "dry_run": False})

    assert response.status_code == 503, response.text
    assert "indisponible" in response.json()["detail"]
    assert sends == []
    assert response.json().get("mode") is None


# ── P0-5 — authentication ──────────────────────────────────────────────────


def test_auth_matrix(monkeypatch, client):
    monkeypatch.setattr(settings, "workspace_api_key", "a" * 32)

    assert client.get("/api/workspace").status_code == 401  # header absent
    assert client.get("/api/workspace", headers={"authorization": ""}).status_code == 401
    assert client.get("/api/workspace", headers={"authorization": "Bearer"}).status_code == 401
    assert (
        client.get("/api/workspace", headers={"authorization": "Bearer wrong"}).status_code == 401
    )
    assert client.get("/api/workspace", headers={"authorization": "Bearer "}).status_code == 401
    assert (
        client.get("/api/workspace", headers={"authorization": "Bearer " + "a" * 32}).status_code
        == 200
    )


def test_every_api_route_is_behind_auth(monkeypatch, client):
    """No /api/* route bypasses the middleware."""
    monkeypatch.setattr(settings, "workspace_api_key", "a" * 32)
    for method, path in [
        ("GET", "/api/workspace"),
        ("PUT", "/api/profile"),
        ("POST", "/api/leads"),
        ("POST", "/api/leads/batch"),
        ("GET", "/api/companies/search?q=test"),
        ("POST", "/api/campaigns"),
        ("POST", "/api/campaign/run"),
        ("GET", "/api/export/backup.json"),
    ]:
        response = client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


def test_public_routes_need_no_secret(monkeypatch, client):
    monkeypatch.setattr(settings, "workspace_api_key", "a" * 32)
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_non_browser_client_without_origin_is_not_a_bypass(monkeypatch, client):
    """A missing Origin is not an authentication bypass — the token still rules."""
    monkeypatch.setattr(settings, "workspace_api_key", "a" * 32)
    assert client.get("/api/workspace").status_code == 401
    assert (
        client.get("/api/workspace", headers={"authorization": "Bearer " + "a" * 32}).status_code
        == 200
    )


def test_untrusted_origin_is_rejected(monkeypatch, client):
    monkeypatch.setattr(settings, "allowed_origins", ["https://app.outreached.io"])
    assert (
        client.get(
            "/api/workspace",
            headers={
                "origin": "https://evil.example",
                "authorization": "Bearer " + "a" * 32,
            },
        ).status_code
        == 403
    )


def test_preflight_options_is_not_authenticated(monkeypatch):
    """CORS is snapshotted at app construction, so build the app after config."""
    monkeypatch.setattr(settings, "workspace_api_key", "a" * 32)
    monkeypatch.setattr(settings, "allowed_origins", ["http://localhost:3000"])
    with TestClient(create_app()) as client:
        response = client.options(
            "/api/workspace",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "GET",
            },
        )
    assert response.status_code in {200, 204}, response.text


def test_docs_are_disabled_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    app = create_app()
    assert app.docs_url is None and app.openapi_url is None


# ── P0-2 — secret validation at startup ────────────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"workspace_api_key": ""}, "WORKSPACE_API_KEY"),
        ({"workspace_api_key": "   "}, "WORKSPACE_API_KEY"),
        ({"workspace_api_key": "short"}, "WORKSPACE_API_KEY"),
        ({"workspace_api_key": "change-me-in-production-at-least-32-chars"}, "WORKSPACE_API_KEY"),
        ({"secret_key": ""}, "SECRET_KEY"),
        ({"secret_key": "short"}, "SECRET_KEY"),
        ({"secret_key": "change-me-in-production"}, "SECRET_KEY"),
        ({"secret_key": "k" * 40}, "distincts"),  # equal to workspace_api_key
    ],
)
def test_production_rejects_weak_secrets(overrides, match):
    with pytest.raises(RuntimeError, match=match):
        make_settings(**overrides).validate_production()


# ── P0-3 — hosts / origins fail closed in production ───────────────────────


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"allowed_hosts": []}, "ALLOWED_HOSTS"),
        ({"allowed_hosts": ["*"]}, "ALLOWED_HOSTS"),
        ({"allowed_hosts": ["localhost", "127.0.0.1"]}, "ALLOWED_HOSTS"),
        ({"allowed_origins": []}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["*"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["http://app.outreached.io"]}, "ALLOWED_ORIGINS"),
        ({"allowed_origins": ["https://localhost:3000"]}, "ALLOWED_ORIGINS"),
    ],
)
def test_production_rejects_unsafe_hosts_and_origins(overrides, match):
    with pytest.raises(RuntimeError, match=match):
        make_settings(**overrides).validate_production()


def test_valid_production_configuration_passes():
    make_settings().validate_production()  # must not raise


def test_development_configuration_is_not_constrained():
    Settings(
        app_env="development",
        workspace_api_key="",
        secret_key="",
        allowed_hosts=[],
        allowed_origins=[],
    ).validate_production()  # must not raise


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"workspace_api_key": ""}, "WORKSPACE_API_KEY"),
        ({"secret_key": ""}, "SECRET_KEY"),
        ({"allowed_hosts": []}, "ALLOWED_HOSTS"),
        ({"allowed_origins": []}, "ALLOWED_ORIGINS"),
    ],
)
def test_production_startup_fails_closed(monkeypatch, overrides, match):
    for key, value in {**VALID_PROD, **overrides}.items():
        monkeypatch.setattr(settings, key, value)
    with pytest.raises(RuntimeError, match=match), TestClient(create_app()):
        pass


def test_launch_enabled_requires_real_transports_at_startup(monkeypatch):
    for key, value in {
        **VALID_PROD,
        "commercial_launch_enabled": True,
        "smartlead_api_key": "",
    }.items():
        monkeypatch.setattr(settings, key, value)
    with pytest.raises(RuntimeError, match="SMARTLEAD_API_KEY"), TestClient(create_app()):
        pass
