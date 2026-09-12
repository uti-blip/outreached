"""Regression tests for outbound evidence, cost and side-effect boundaries."""

import copy
import json
from dataclasses import asdict

import httpx
import pytest
from typer.testing import CliRunner

from backend.app.adapters.base import (
    EmailResult,
    LiveSendNotConfiguredError,
    ProviderCapabilityUnavailableError,
)
from backend.app.adapters.mocks import MockEmailAdapter, MockLinkedInAdapter
from backend.app.adapters.smartlead import ApolloAdapter, SmartleadAdapter, UnipileAdapter
from backend.app.campaign_runner import run_campaign
from backend.app.campaign_runner_mock import run_campaign_mock
from backend.app.cli import app as cli
from backend.app.config import settings
from scripts.smartlead_setup import app as smartlead_cli


@pytest.fixture
def forbid_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Preview or unsupported live operation attempted external I/O")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("backend.app.db.supabase.init_db", forbidden)
    monkeypatch.setattr("backend.app.agents.base.BaseAgent.run", forbidden)
    for field in ("deepseek_api_key", "kimi_api_key", "anthropic_api_key", "smartlead_api_key"):
        monkeypatch.setattr(settings, field, "configured-test-key")
    monkeypatch.setattr(settings, "commercial_launch_enabled", True)


@pytest.mark.asyncio
async def test_preview_is_repeatable_and_does_not_invent_commercial_evidence(
    forbid_external_effects,
):
    leads = [
        {"company_name": "Alpha", "domain": "alpha.test", "email": "alice@alpha.test"},
        {"company_name": "Beta", "domain": "beta.test"},
    ]
    original = copy.deepcopy(leads)
    first = await run_campaign(leads)
    second = await run_campaign_mock(leads)
    assert asdict(first) == asdict(second)
    assert leads == original
    assert first.leads_processed == 2
    assert first.mode == "demo"
    assert first.sequences_generated == first.replies_classified == first.messages_sent == 0
    assert first.total_cost_eur == first.total_latency_ms == 0
    assert not first.errors
    assert [lead["company"] for lead in first.details] == ["Alpha", "Beta"]
    assert first.details[0]["supplied_channels"] == ["email"]
    assert first.details[1]["supplied_channels"] == []
    for lead in first.details:
        for step in lead["steps"]:
            assert "external_id" not in step
            assert "intent" not in step
            assert "score" not in step
            assert "body" not in step


@pytest.mark.asyncio
async def test_live_campaign_refuses_before_initialization(forbid_external_effects):
    with pytest.raises(LiveSendNotConfiguredError):
        await run_campaign([{"company_name": "Alpha"}], dry_run=False)


@pytest.mark.asyncio
async def test_invalid_input_is_not_reported_as_a_processed_company(forbid_external_effects):
    with pytest.raises(ValueError):
        await run_campaign(["not an object"])
    result = await run_campaign([{}, {"company_name": "  "}])
    assert len(result.errors) == 2
    assert all(item["status"] == "invalid" for item in result.details)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key", ["", "configured-test-key"])
async def test_direct_real_adapter_calls_cannot_bypass_live_gate(api_key, forbid_external_effects):
    with pytest.raises(LiveSendNotConfiguredError):
        await SmartleadAdapter(api_key).send("a@example.test", "subject", "body", dry_run=False)
    with pytest.raises(LiveSendNotConfiguredError):
        await UnipileAdapter(api_key).send_message("https://linkedin.com/in/test", "body", False)
    with pytest.raises(LiveSendNotConfiguredError):
        await UnipileAdapter(api_key).send_connection("https://linkedin.com/in/test", "body", False)


@pytest.mark.asyncio
async def test_dry_adapter_results_do_not_claim_delivery(forbid_external_effects):
    email = MockEmailAdapter()
    result = await SmartleadAdapter("test-key").send("a@example.test", "subject", "body")
    assert result.status == "dry_run"
    assert await email.check_status(result.external_id) == "dry_run"
    assert await email.check_status("unobserved-provider-id") == "unknown"
    assert EmailResult(success=False).status != "sent"
    linkedin = await MockLinkedInAdapter().send_message("https://linkedin.com/in/test", "body")
    assert linkedin.status == "dry_run"


@pytest.mark.asyncio
async def test_unimplemented_provider_operations_fail_honestly(forbid_external_effects):
    with pytest.raises(ProviderCapabilityUnavailableError):
        await ApolloAdapter("test-key").enrich("Alpha", "alpha.test")
    with pytest.raises(ProviderCapabilityUnavailableError):
        await ApolloAdapter("test-key").search_contacts("Alpha")
    with pytest.raises(ProviderCapabilityUnavailableError):
        await SmartleadAdapter("test-key").check_status("unknown")


def test_cli_preview_only_writes_explicit_output(tmp_path, monkeypatch, forbid_external_effects):
    monkeypatch.chdir(tmp_path)
    seed = tmp_path / "seeds.json"
    seed.write_text(json.dumps([{"company_name": "Alpha"}]), encoding="utf-8")
    runner = CliRunner()
    preview = runner.invoke(cli, ["run-campaign", "--seed-list", str(seed), "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert "Messages sent: 0" in preview.output
    assert list(tmp_path.iterdir()) == [seed]
    report = tmp_path / "report.json"
    explicit = runner.invoke(
        cli, ["run-campaign", "--seed-list", str(seed), "--output", str(report)]
    )
    assert explicit.exit_code == 0, explicit.output
    assert json.loads(report.read_text())["mode"] == "demo"
    live = runner.invoke(cli, ["run-campaign", "--seed-list", str(seed), "--live"])
    assert live.exit_code == 1
    assert "indisponible" in live.output


def test_cli_invalid_seed_returns_failure(tmp_path, forbid_external_effects):
    seed = tmp_path / "seeds.json"
    seed.write_text("[1]", encoding="utf-8")
    result = CliRunner().invoke(cli, ["run-campaign", "--seed-list", str(seed)])
    assert result.exit_code == 1
    assert "array of objects" in result.output


def test_smartlead_setup_defaults_to_local_plan(forbid_external_effects):
    runner = CliRunner()
    result = runner.invoke(smartlead_cli, ["provision", "--domain", "example.test", "--count", "2"])
    assert result.exit_code == 0, result.output
    assert "no mailboxes created" in result.output
    assert result.output.count("Planned mailbox:") == 2
    live = runner.invoke(smartlead_cli, ["provision", "--domain", "example.test", "--live"])
    assert live.exit_code == 1
    assert "unavailable" in live.output
    assert runner.invoke(smartlead_cli, ["status"]).exit_code == 1
