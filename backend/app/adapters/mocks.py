"""Mock adapter implementations — injectable for testing.

These return controlled, deterministic data with zero network calls.
"""

from backend.app.adapters.base import (
    EmailAdapter,
    EmailResult,
    EnrichmentAdapter,
    EnrichmentResult,
    LinkedInAdapter,
    LinkedInResult,
)

# ── Email mock ────────────────────────────────────────


class MockEmailAdapter(EmailAdapter):
    """Always succeeds in dry-run; fails in live mode (safety)."""

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_email: str = "",
        dry_run: bool = True,
    ) -> EmailResult:
        if dry_run:
            return EmailResult(
                success=True,
                external_id=f"mock-email-{hash(to_email) & 0xFFFF:04x}",
                status="dry_run",
            )
        return EmailResult(
            success=False,
            error="MockEmailAdapter: live mode disabled — no real sends in test/dev",
        )

    async def check_status(self, external_id: str) -> str:
        return "delivered"


# ── Enrichment mock ───────────────────────────────────


class MockEnrichmentAdapter(EnrichmentAdapter):
    """Returns canned firmographic data."""

    async def enrich(
        self,
        company_name: str,
        domain: str = "",
    ) -> EnrichmentResult:
        return EnrichmentResult(
            company_name=company_name,
            domain=domain or f"{company_name.lower().replace(' ', '')}.com",
            size="50-200",
            industry="SaaS / Logiciels",
            tech_stack=["AWS", "PostgreSQL", "React", "Python"],
            signals=["Hiring engineers", "Recent Series A", "Product-led growth"],
        )

    async def search_contacts(
        self,
        company_name: str,
        titles: list[str] | None = None,
    ) -> list[dict]:
        return [
            {
                "first_name": "Jean",
                "last_name": "Dupont",
                "title": "CTO",
                "email": f"jean@{company_name.lower().replace(' ', '')}.fr",
                "linkedin_url": "https://linkedin.com/in/jeandupont",
            },
            {
                "first_name": "Marie",
                "last_name": "Martin",
                "title": "VP Engineering",
                "email": f"marie@{company_name.lower().replace(' ', '')}.fr",
                "linkedin_url": "https://linkedin.com/in/mariemartin",
            },
        ]


# ── LinkedIn mock ─────────────────────────────────────


class MockLinkedInAdapter(LinkedInAdapter):
    """Always succeeds in dry-run."""

    async def send_connection(
        self,
        linkedin_url: str,
        message: str = "",
        dry_run: bool = True,
    ) -> LinkedInResult:
        if dry_run:
            return LinkedInResult(
                success=True,
                external_id=f"mock-li-conn-{hash(linkedin_url) & 0xFFFF:04x}",
                action="connection_request",
            )
        return LinkedInResult(
            success=False,
            error="MockLinkedInAdapter: live mode disabled",
        )

    async def send_message(
        self,
        linkedin_url: str,
        message: str,
        dry_run: bool = True,
    ) -> LinkedInResult:
        if dry_run:
            return LinkedInResult(
                success=True,
                external_id=f"mock-li-msg-{hash(linkedin_url) & 0xFFFF:04x}",
                action="message",
            )
        return LinkedInResult(
            success=False,
            error="MockLinkedInAdapter: live mode disabled",
        )
