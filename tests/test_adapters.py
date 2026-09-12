"""Tests: Adapters (Cluster 4) — contracts + mocks."""

import pytest

from backend.app.adapters.mocks import (
    MockEmailAdapter,
    MockEnrichmentAdapter,
    MockLinkedInAdapter,
)


class TestEmailAdapter:
    @pytest.mark.asyncio
    async def test_dry_run_succeeds(self):
        adapter = MockEmailAdapter()
        result = await adapter.send(
            to_email="test@example.com",
            subject="Test",
            body="Hello",
            dry_run=True,
        )
        assert result.success
        assert result.status == "dry_run"
        assert result.external_id is not None

    @pytest.mark.asyncio
    async def test_live_mode_fails(self):
        """Mock adapter must NOT allow real sends."""
        adapter = MockEmailAdapter()
        result = await adapter.send(
            to_email="test@example.com",
            subject="Test",
            body="Hello",
            dry_run=False,
        )
        assert not result.success
        assert "live mode disabled" in result.error.lower()  # type: ignore

    @pytest.mark.asyncio
    async def test_check_status(self):
        adapter = MockEmailAdapter()
        status = await adapter.check_status("any-id")
        assert status == "unknown"


class TestEnrichmentAdapter:
    @pytest.mark.asyncio
    async def test_enrich_returns_data(self):
        adapter = MockEnrichmentAdapter()
        result = await adapter.enrich("Acme Corp", "acme.com")
        assert result.company_name == "Acme Corp"
        assert result.industry == "SaaS / Logiciels"
        assert len(result.tech_stack) > 0

    @pytest.mark.asyncio
    async def test_search_contacts_returns_contacts(self):
        adapter = MockEnrichmentAdapter()
        contacts = await adapter.search_contacts("Acme Corp")
        assert len(contacts) == 2
        assert contacts[0]["title"] == "CTO"


class TestLinkedInAdapter:
    @pytest.mark.asyncio
    async def test_connection_dry_run(self):
        adapter = MockLinkedInAdapter()
        result = await adapter.send_connection(
            linkedin_url="https://linkedin.com/in/test",
            message="Hello",
            dry_run=True,
        )
        assert result.success
        assert result.action == "connection_request"

    @pytest.mark.asyncio
    async def test_message_dry_run(self):
        adapter = MockLinkedInAdapter()
        result = await adapter.send_message(
            linkedin_url="https://linkedin.com/in/test",
            message="Hello",
            dry_run=True,
        )
        assert result.success
        assert result.action == "message"

    @pytest.mark.asyncio
    async def test_live_mode_fails(self):
        adapter = MockLinkedInAdapter()
        result = await adapter.send_message(
            linkedin_url="https://linkedin.com/in/test",
            message="Hello",
            dry_run=False,
        )
        assert not result.success
        assert "live mode disabled" in result.error.lower()  # type: ignore
