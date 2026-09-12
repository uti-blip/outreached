"""Agent output and legacy persistence must fail honestly without lost costs."""

from unittest.mock import Mock

import pytest

from backend.app import cost_tracker
from backend.app.agents.base import ICPScoringAgent
from backend.app.config import settings
from backend.app.db import supabase
from backend.app.llm.provider import LLMProvider, LLMResponse, ProviderKind
from backend.app.llm.router import InferenceRouter
from backend.app.rag.playbook_store import PlaybookStore


class StubProvider(LLMProvider):
    def __init__(self, kind: ProviderKind, response_text: str):
        self.kind = kind
        self.model = "test-model"
        self.response_text = response_text
        self.calls = 0

    async def complete(self, prompt, system=None, temperature=0.7, max_tokens=2048):
        self.calls += 1
        return LLMResponse(self.response_text, self.model, 10, 5, 0)

    def cost_eur(self, input_tokens: int, output_tokens: int) -> float:
        return 0.01


@pytest.mark.asyncio
async def test_injected_providers_are_used_and_failed_output_cost_is_retained():
    preferred = StubProvider(ProviderKind.KIMI, "This is not valid JSON")
    fallback = StubProvider(ProviderKind.DEEPSEEK, '{"score": 70, "verdict": "go"}')
    router = InferenceRouter()
    router._providers = {ProviderKind.KIMI: preferred, ProviderKind.DEEPSEEK: fallback}
    result = await ICPScoringAgent(router).run({"enriched": {"name": "Alpha"}})
    assert result.success
    assert result.output["score"] == 70
    assert preferred.calls == fallback.calls == 1
    assert result.input_tokens == 20
    assert result.output_tokens == 10
    assert result.cost_eur == 0.02


@pytest.mark.asyncio
async def test_invalid_final_output_is_a_failure_with_reported_usage():
    router = InferenceRouter()
    provider = StubProvider(ProviderKind.KIMI, "[]")
    router._providers = {ProviderKind.KIMI: provider}
    result = await ICPScoringAgent(router).run({})
    assert not result.success
    assert result.cost_eur == 0.01
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.error


@pytest.mark.asyncio
async def test_no_provider_has_an_explicit_error():
    router = InferenceRouter()
    router._providers = {}
    result = await ICPScoringAgent(router).run({})
    assert not result.success
    assert result.error == "No LLM providers configured."


@pytest.mark.asyncio
async def test_cloud_audit_failure_never_falls_back_to_a_different_store(monkeypatch):
    client = Mock()
    client.table.return_value.insert.return_value.execute.side_effect = RuntimeError("Store down")
    monkeypatch.setattr(cost_tracker, "get_supabase", lambda: client)

    def sqlite_is_forbidden():
        pytest.fail("Cloud audit failure must not switch to SQLite")

    monkeypatch.setattr(cost_tracker, "_get_sqlite", sqlite_is_forbidden)
    with pytest.raises(RuntimeError, match="Store down"):
        await cost_tracker.log_agent_run("tenant", "scoring", "test", 10, 5, 0.01, 2)


def test_production_cannot_create_a_legacy_development_database(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_key", "")
    with pytest.raises(RuntimeError, match="requires Supabase"):
        supabase.get_supabase()
    with pytest.raises(RuntimeError, match="unavailable in production"):
        supabase._get_sqlite()
    assert not supabase.DB_PATH.exists()


def test_partial_supabase_configuration_is_not_treated_as_offline(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "")
    with pytest.raises(RuntimeError, match="configured together"):
        supabase.get_supabase()


def test_configured_supabase_initialization_failure_does_not_fall_back(monkeypatch):
    import supabase as provider_library

    monkeypatch.setattr(settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "test-key")
    monkeypatch.setattr(supabase, "_use_supabase", None)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Client initialization failed")

    monkeypatch.setattr(provider_library, "create_client", unavailable)
    with pytest.raises(RuntimeError, match="no SQLite fallback"):
        supabase.get_supabase()
    assert supabase._client is None
    assert supabase._use_supabase is None
    assert not supabase.DB_PATH.exists()


def test_vector_storage_is_explicitly_unavailable():
    with pytest.raises(NotImplementedError, match="embedding"):
        PlaybookStore().add_chunk("playbook", "proof", "Example", embedding=[0.1, 0.2])
