"""Resolve preview adapters; live transports are deliberately unavailable."""

from backend.app.adapters.base import EmailAdapter, LinkedInAdapter, LiveSendNotConfiguredError

LIVE_SEND_UNAVAILABLE = (
    "Envoi réel indisponible : les intégrations fournisseurs, la persistance des messages, "
    "l’idempotence, les désinscriptions et le suivi des réponses doivent être validés."
)


def resolve_send_adapters(dry_run: bool) -> tuple[EmailAdapter, LinkedInAdapter]:
    """Return zero-network mocks for previews or fail before any live operation.

    Supplying provider credentials or changing a feature flag cannot activate
    the incomplete sending pipeline.
    """
    if not dry_run:
        raise LiveSendNotConfiguredError(LIVE_SEND_UNAVAILABLE)

    from backend.app.adapters.mocks import MockEmailAdapter, MockLinkedInAdapter

    return MockEmailAdapter(), MockLinkedInAdapter()
