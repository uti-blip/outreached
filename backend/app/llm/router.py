"""Tiered inference router.

Routes each agent task to the optimal model:
- deepseek-v4-flash: enrichment, classification simple (volume, cheap)
- kimi-k2.6: scoring ICP, standard sequences (good quality/price ratio)
- claude-sonnet: objection complexe, high-value personalization

Cost is the hard constraint: the router must maximize quality under cost budget.
"""

from enum import StrEnum

from backend.app.config import settings
from backend.app.llm.provider import (
    AnthropicProvider,
    DeepSeekProvider,
    KimiProvider,
    LLMProvider,
    ProviderKind,
)


class TaskComplexity(StrEnum):
    """Broad task category → model selection."""

    VOLUME = "volume"  # enrichment, dedup, simple classification → deepseek-v4-flash
    STANDARD = "standard"  # scoring, standard sequences → kimi-k2.6
    COMPLEX = "complex"  # objection handling, high-value personalization → claude-sonnet


# Agent type → default complexity
AGENT_COMPLEXITY = {
    "sourcing": TaskComplexity.VOLUME,
    "enrichment": TaskComplexity.VOLUME,
    "icp_scoring": TaskComplexity.STANDARD,
    "sequence_writer": TaskComplexity.STANDARD,
    "reply_classifier": TaskComplexity.VOLUME,
    "reply_drafter": TaskComplexity.COMPLEX,
}

# Complexity → provider kind
COMPLEXITY_PROVIDER = {
    TaskComplexity.VOLUME: ProviderKind.DEEPSEEK,
    TaskComplexity.STANDARD: ProviderKind.KIMI,
    TaskComplexity.COMPLEX: ProviderKind.ANTHROPIC,
}

# Provider kind → model name
PROVIDER_MODEL = {
    ProviderKind.DEEPSEEK: "deepseek-v4-flash",
    ProviderKind.KIMI: "kimi-k2.6",
    ProviderKind.ANTHROPIC: "claude-sonnet-4-20250514",
}


class InferenceRouter:
    """Routes an agent task to the correct LLM provider + model."""

    def __init__(self):
        self._providers: dict[ProviderKind, LLMProvider | None] = {}

        # DeepSeek
        if settings.deepseek_api_key:
            self._providers[ProviderKind.DEEPSEEK] = DeepSeekProvider(
                api_key=settings.deepseek_api_key,
                model=PROVIDER_MODEL[ProviderKind.DEEPSEEK],
            )

        # Kimi
        if settings.kimi_api_key:
            self._providers[ProviderKind.KIMI] = KimiProvider(
                api_key=settings.kimi_api_key,
                model=PROVIDER_MODEL[ProviderKind.KIMI],
            )

        # Anthropic
        if settings.anthropic_api_key:
            self._providers[ProviderKind.ANTHROPIC] = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=PROVIDER_MODEL[ProviderKind.ANTHROPIC],
            )

    def get_provider(self, agent_type: str) -> LLMProvider:
        """Return the provider for a given agent type, with fallback.

        Fallback chain: complexity → next simpler complexity → next simpler.
        If no providers at all, raise ValueError.
        """
        complexity = AGENT_COMPLEXITY.get(agent_type, TaskComplexity.VOLUME)
        preferred = COMPLEXITY_PROVIDER[complexity]

        # Try the preferred provider
        if self._providers.get(preferred):
            return self._providers[preferred]

        # Fallback: try all configured providers in order of cost (cheapest first)
        for kind in (ProviderKind.DEEPSEEK, ProviderKind.KIMI, ProviderKind.ANTHROPIC):
            if self._providers.get(kind):
                return self._providers[kind]

        raise ValueError("No LLM providers configured. Set at least one API key.")

    def get_model_for_agent(self, agent_type: str) -> str:
        """Return the model name that will be used for this agent type."""
        provider = self.get_provider(agent_type)
        return provider.model

    def cost_eur(self, agent_type: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for a task."""
        provider = self.get_provider(agent_type)
        return provider.cost_eur(input_tokens, output_tokens)


# ── Singleton ──────────────────────────────────────────
_router: InferenceRouter | None = None


def get_router() -> InferenceRouter:
    global _router
    if _router is None:
        _router = InferenceRouter()
    return _router
