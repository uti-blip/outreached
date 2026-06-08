"""Real adapter implementations — thin wrappers around external APIs.

Phase 1: stub implementations that will be wired to actual APIs.
Smartlead / Instantly / Apollo / Unipile have REST APIs.
"""

import httpx

from backend.app.adapters.base import (
    EmailAdapter,
    EmailResult,
    EnrichmentAdapter,
    EnrichmentResult,
    LinkedInAdapter,
    LinkedInResult,
)
from backend.app.config import settings

# ── Smartlead Email ───────────────────────────────────


class SmartleadAdapter(EmailAdapter):
    """Email via Smartlead API. Dry-run by default per app config."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or settings.smartlead_api_key
        self.base_url = "https://server.smartlead.ai/api/v1"

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_email: str = "",
        dry_run: bool = True,
    ) -> EmailResult:
        if dry_run or not self.api_key:
            return EmailResult(
                success=True,
                external_id=f"dryrun-{hash(to_email) & 0xFFFF:04x}",
                status="dry_run",
            )

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/send-email",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "to": to_email,
                    "subject": subject,
                    "body": body,
                    "from": from_email,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return EmailResult(success=True, external_id=data.get("id"))
            return EmailResult(success=False, error=resp.text)

    async def check_status(self, external_id: str) -> str:
        if not self.api_key:
            return "unknown"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/email-status/{external_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code == 200:
                return resp.json().get("status", "unknown")
            return "error"


# ── Apollo Enrichment ─────────────────────────────────


class ApolloAdapter(EnrichmentAdapter):
    """Enrichment via Apollo.io API."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or settings.apollo_api_key
        self.base_url = "https://api.apollo.io/v1"

    async def enrich(
        self,
        company_name: str,
        domain: str = "",
    ) -> EnrichmentResult:
        if not self.api_key:
            return EnrichmentResult(company_name=company_name, domain=domain)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/organizations/enrich",
                headers={"x-api-key": self.api_key},
                json={"domain": domain} if domain else {"name": company_name},
            )
            if resp.status_code == 200:
                data = resp.json().get("organization", {})
                return EnrichmentResult(
                    company_name=data.get("name", company_name),
                    domain=data.get("domain", domain),
                    size=data.get("employee_count", ""),
                    industry=data.get("industry", ""),
                    raw=data,
                )
            return EnrichmentResult(company_name=company_name, domain=domain)

    async def search_contacts(
        self,
        company_name: str,
        titles: list[str] | None = None,
    ) -> list[dict]:
        if not self.api_key:
            return []

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/people/search",
                headers={"x-api-key": self.api_key},
                json={
                    "organization_name": company_name,
                    "titles": titles or ["CTO", "VP Engineering", "Head of Engineering"],
                },
            )
            if resp.status_code == 200:
                return resp.json().get("people", [])
            return []


# ── Unipile LinkedIn ──────────────────────────────────


class UnipileAdapter(LinkedInAdapter):
    """LinkedIn via Unipile API."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or settings.unipile_api_key
        self.base_url = "https://api.unipile.com/v1"

    async def send_connection(
        self,
        linkedin_url: str,
        message: str = "",
        dry_run: bool = True,
    ) -> LinkedInResult:
        if dry_run or not self.api_key:
            return LinkedInResult(
                success=True,
                external_id=f"dryrun-li-{hash(linkedin_url) & 0xFFFF:04x}",
                action="connection_request",
            )

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/linkedin/connections",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"profile_url": linkedin_url, "message": message},
            )
            if resp.status_code == 200:
                return LinkedInResult(
                    success=True,
                    external_id=resp.json().get("id"),
                    action="connection_request",
                )
            return LinkedInResult(success=False, error=resp.text, action="connection_request")

    async def send_message(
        self,
        linkedin_url: str,
        message: str,
        dry_run: bool = True,
    ) -> LinkedInResult:
        if dry_run or not self.api_key:
            return LinkedInResult(
                success=True,
                external_id=f"dryrun-li-msg-{hash(linkedin_url) & 0xFFFF:04x}",
                action="message",
            )

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/linkedin/messages",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"profile_url": linkedin_url, "message": message},
            )
            if resp.status_code == 200:
                return LinkedInResult(
                    success=True,
                    external_id=resp.json().get("id"),
                    action="message",
                )
            return LinkedInResult(success=False, error=resp.text, action="message")
