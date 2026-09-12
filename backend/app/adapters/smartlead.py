"""Provider boundaries with explicit capability failures.

The original endpoints were unverified placeholders. No production traffic may
use them. Dry runs delegate to local mocks; real calls fail before networking.
"""

from backend.app.adapters.base import (
    EmailAdapter,
    EmailResult,
    EnrichmentAdapter,
    EnrichmentResult,
    LinkedInAdapter,
    LinkedInResult,
    LiveSendNotConfiguredError,
    ProviderCapabilityUnavailableError,
)
from backend.app.adapters.factory import LIVE_SEND_UNAVAILABLE
from backend.app.adapters.mocks import MockEmailAdapter, MockLinkedInAdapter


class SmartleadAdapter(EmailAdapter):
    """Reserved Smartlead integration. Only local previews are implemented."""

    def __init__(self, api_key: str = ""):
        # Retain the constructor contract; credentials cannot enable a stub.
        pass

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_email: str = "",
        dry_run: bool = True,
    ) -> EmailResult:
        if not dry_run:
            raise LiveSendNotConfiguredError(LIVE_SEND_UNAVAILABLE)
        return await MockEmailAdapter().send(to_email, subject, body, from_email, dry_run=True)

    async def check_status(self, external_id: str) -> str:
        raise ProviderCapabilityUnavailableError(
            "Le suivi de livraison Smartlead n’est pas implémenté."
        )


class ApolloAdapter(EnrichmentAdapter):
    """Reserved enrichment integration; never report an unavailable lookup as data."""

    def __init__(self, api_key: str = ""):
        pass

    async def enrich(self, company_name: str, domain: str = "") -> EnrichmentResult:
        raise ProviderCapabilityUnavailableError("L’enrichissement Apollo n’est pas implémenté.")

    async def search_contacts(
        self,
        company_name: str,
        titles: list[str] | None = None,
    ) -> list[dict]:
        raise ProviderCapabilityUnavailableError("La recherche Apollo n’est pas implémentée.")


class UnipileAdapter(LinkedInAdapter):
    """Reserved Unipile integration. Only local previews are implemented."""

    def __init__(self, api_key: str = ""):
        pass

    async def send_connection(
        self,
        linkedin_url: str,
        message: str = "",
        dry_run: bool = True,
    ) -> LinkedInResult:
        if not dry_run:
            raise LiveSendNotConfiguredError(LIVE_SEND_UNAVAILABLE)
        return await MockLinkedInAdapter().send_connection(linkedin_url, message, dry_run=True)

    async def send_message(
        self,
        linkedin_url: str,
        message: str,
        dry_run: bool = True,
    ) -> LinkedInResult:
        if not dry_run:
            raise LiveSendNotConfiguredError(LIVE_SEND_UNAVAILABLE)
        return await MockLinkedInAdapter().send_message(linkedin_url, message, dry_run=True)
