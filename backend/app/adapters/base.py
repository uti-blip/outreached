"""Abstract adapter interfaces — thin, mockable, dry-run by default.

All adapters follow the same pattern:
- An abstract base class defining the contract
- A real implementation (wraps external API)
- A mock implementation (returns canned data, injectable in tests)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ProviderCapabilityUnavailableError(RuntimeError):
    """The requested provider operation has not been implemented and verified."""


class LiveSendNotConfiguredError(ProviderCapabilityUnavailableError):
    """Live sending is unavailable, regardless of installed credentials."""


# ── Email ─────────────────────────────────────────────


@dataclass
class EmailResult:
    success: bool
    external_id: str | None = None
    status: str = "unknown"
    error: str | None = None


class EmailAdapter(ABC):
    """Send outreach emails via Smartlead/Instantly. Dry-run by default."""

    @abstractmethod
    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_email: str = "",
        dry_run: bool = True,
    ) -> EmailResult: ...

    @abstractmethod
    async def check_status(self, external_id: str) -> str: ...


# ── Enrichment ────────────────────────────────────────


@dataclass
class EnrichmentResult:
    company_name: str
    domain: str = ""
    size: str = ""
    industry: str = ""
    tech_stack: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class EnrichmentAdapter(ABC):
    """Enrich leads with firmographics + buying signals via Apollo."""

    @abstractmethod
    async def enrich(
        self,
        company_name: str,
        domain: str = "",
    ) -> EnrichmentResult: ...

    @abstractmethod
    async def search_contacts(
        self,
        company_name: str,
        titles: list[str] | None = None,
    ) -> list[dict]: ...


# ── LinkedIn ──────────────────────────────────────────


@dataclass
class LinkedInResult:
    success: bool
    external_id: str | None = None
    action: str = ""  # connection_request, message, inmail
    error: str | None = None
    status: str = "unknown"


class LinkedInAdapter(ABC):
    """LinkedIn outreach via Unipile. Dry-run by default."""

    @abstractmethod
    async def send_connection(
        self,
        linkedin_url: str,
        message: str = "",
        dry_run: bool = True,
    ) -> LinkedInResult: ...

    @abstractmethod
    async def send_message(
        self,
        linkedin_url: str,
        message: str,
        dry_run: bool = True,
    ) -> LinkedInResult: ...
