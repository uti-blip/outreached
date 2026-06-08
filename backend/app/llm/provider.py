"""LLM Provider abstraction — unified interface for DeepSeek, Kimi, Anthropic.

All providers expose the same `async complete(prompt, system, **kwargs)` interface.
Cost tracking is handled by the caller (cost_tracker.py), not here.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class ProviderKind(StrEnum):
    DEEPSEEK = "deepseek"
    KIMI = "kimi"
    ANTHROPIC = "anthropic"


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMProvider(ABC):
    """Abstract LLM provider. Implementations handle the HTTP call."""

    kind: ProviderKind
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> LLMResponse: ...

    def cost_eur(self, input_tokens: int, output_tokens: int) -> float:
        """Per-model pricing in EUR. Override in subclass.

        Pricing (June 2026, approximate):
        - DeepSeek v4-flash: $0.14/$0.28 per 1M input/output → ~€0.13/€0.26
        - Kimi K2.6: ~$0.60/$2.40 per 1M
        - Claude Sonnet: $3.00/$15.00 per 1M
        """
        return 0.0


# ── Concrete providers ─────────────────────────────────


class DeepSeekProvider(LLMProvider):
    kind = ProviderKind.DEEPSEEK

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
        self.api_key = api_key
        self.model = model

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        import time

        import httpx

        start = time.monotonic()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        latency = int((time.monotonic() - start) * 1000)

        return LLMResponse(
            text=choice["message"]["content"],
            model=self.model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency,
        )

    def cost_eur(self, input_tokens: int, output_tokens: int) -> float:
        # $0.14/1M input, $0.28/1M output → EUR (approx 0.93x)
        return (input_tokens * 0.14 + output_tokens * 0.28) / 1_000_000 * 0.93


class KimiProvider(LLMProvider):
    kind = ProviderKind.KIMI

    def __init__(self, api_key: str, model: str = "kimi-k2.6"):
        self.api_key = api_key
        self.model = model

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        import time

        import httpx

        start = time.monotonic()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            resp = await client.post(
                "https://api.moonshot.cn/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        latency = int((time.monotonic() - start) * 1000)

        return LLMResponse(
            text=choice["message"]["content"],
            model=self.model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency,
        )

    def cost_eur(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * 0.60 + output_tokens * 2.40) / 1_000_000 * 0.93


class AnthropicProvider(LLMProvider):
    kind = ProviderKind.ANTHROPIC

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        import time

        import httpx

        start = time.monotonic()

        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system

        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        latency = int((time.monotonic() - start) * 1000)
        content = data["content"]
        text = content[0]["text"] if isinstance(content, list) else content

        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=data["usage"]["input_tokens"],
            output_tokens=data["usage"]["output_tokens"],
            latency_ms=latency,
        )

    def cost_eur(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * 3.00 + output_tokens * 15.00) / 1_000_000 * 0.93
