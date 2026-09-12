"""Base agent — shared inference, cost tracking, and retry logic."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from backend.app.llm.router import InferenceRouter, get_router


@dataclass
class AgentResult:
    """Output of any agent run."""

    agent_type: str
    success: bool
    output: dict
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_eur: float = 0.0
    latency_ms: int = 0
    error: str | None = None


class BaseAgent(ABC):
    """Base class for all 6 agent roles.

    Subclasses implement `_system_prompt()` and `_build_prompt(data)`.
    The base handles: router selection, completion, cost tracking, error handling.
    """

    agent_type: str = "base"

    def __init__(self, router: InferenceRouter | None = None):
        self._router = router or get_router()

    @abstractmethod
    def _system_prompt(self) -> str: ...

    @abstractmethod
    def _build_prompt(self, data: dict) -> str: ...

    async def run(self, data: dict) -> AgentResult:
        """Execute the agent: build prompt → call LLM (with fallback) → return result."""
        import time as _time

        # Try preferred provider, then fall back through the chain
        errors = []
        input_tokens = output_tokens = latency = 0
        cost = 0.0
        start = _time.monotonic()
        for provider in self._get_provider_chain():
            model = provider.model
            try:
                response = await provider.complete(
                    prompt=self._build_prompt(data),
                    system=self._system_prompt(),
                    temperature=self._temperature(),
                    max_tokens=self._max_tokens(),
                )
                latency = int((_time.monotonic() - start) * 1000)
                # A completed response can be billable even when JSON is invalid.
                input_tokens += response.input_tokens
                output_tokens += response.output_tokens
                cost += provider.cost_eur(response.input_tokens, response.output_tokens)
                return AgentResult(
                    agent_type=self.agent_type,
                    success=True,
                    output=self._parse_response(response.text),
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_eur=round(cost, 6),
                    latency_ms=latency,
                )
            except Exception as exc:
                latency = int((_time.monotonic() - start) * 1000)
                # Provider errors can contain request data or credential-bearing URLs.
                errors.append(f"[{provider.kind.value}] {type(exc).__name__}")

        # All providers failed
        return AgentResult(
            agent_type=self.agent_type,
            success=False,
            output={},
            model="none",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_eur=round(cost, 6),
            latency_ms=latency,
            error="; ".join(errors) or "No LLM providers configured.",
        )

    def _get_provider_chain(self) -> list:
        """Respect the injected router; never recreate providers from ambient credentials."""
        return self._router.get_provider_chain(self.agent_type)

    def _temperature(self) -> float:
        return 0.3  # default: low temp for structured tasks

    def _max_tokens(self) -> int:
        return 2048

    def _parse_response(self, text: str) -> dict:
        """Require a JSON object instead of claiming unstructured text is valid output."""
        import json

        # Try to extract JSON block
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Agent output must be a JSON object")
        return parsed


# ── Agent implementations ──────────────────────────────


class SourcingAgent(BaseAgent):
    """Builds/expands target lists from ICP criteria."""

    agent_type = "sourcing"

    def _system_prompt(self) -> str:
        return """You are a B2B sourcing agent specialized in SaaS France.
Given an ICP (Ideal Customer Profile), find or validate companies that match.
Return JSON: {"companies": [{"name": "...", "domain": "...", "why": "..."}]}"""

    def _build_prompt(self, data: dict) -> str:
        icp = data.get("icp", "SaaS B2B France, 10-200 employees, raised Series A+")
        return f"ICP: {icp}\n\nSuggest 5 target companies in France matching this profile. Return JSON."


class EnrichmentAgent(BaseAgent):
    """Enriches leads with firmographics and buying signals."""

    agent_type = "enrichment"

    def _system_prompt(self) -> str:
        return """You are a B2B enrichment agent. Given a company name and domain,
fill in firmographic data and identify buying signals.
Return JSON: {"name": "...", "size": "...", "industry": "...", "tech_stack": [...], "signals": [...]}"""

    def _build_prompt(self, data: dict) -> str:
        company = data.get("company_name", "Unknown")
        domain = data.get("domain", "")
        return f"Enrich: {company} ({domain}). Return firmographics + signals as JSON."

    def _temperature(self) -> float:
        return 0.1  # deterministic for enrichment


class ICPScoringAgent(BaseAgent):
    """Scores each lead against the vertical playbook."""

    agent_type = "icp_scoring"

    def _system_prompt(self) -> str:
        return """You are an ICP scoring agent for B2B SaaS France outbound.
Score each lead on: company fit (0-50), signal strength (0-30), timing (0-20).
Total max: 100. Threshold for outreach: 60.
Return JSON: {"score": N, "breakdown": {"fit": N, "signals": N, "timing": N}, "verdict": "go"|"nurture"|"reject", "reasoning": "..."}"""

    def _build_prompt(self, data: dict) -> str:
        enriched = data.get("enriched", {})
        playbook = data.get("playbook_context", "")
        return f"Playbook context:\n{playbook}\n\nLead data:\n{enriched}\n\nScore this lead. Return JSON."


class SequenceWriterAgent(BaseAgent):
    """Generates personalized multi-touch sequences using RAG playbook."""

    agent_type = "sequence_writer"

    def _system_prompt(self) -> str:
        return """You are a B2B outbound sequence writer for SaaS France.
Generate a multi-step outreach sequence (email + LinkedIn) personalized to the lead.
Use the playbook context for objections, pricing, and proof points.
Each message MUST contain: opt-out link + sender identity (CNIL B2B compliance).
Return JSON: {"steps": [{"step": 1, "channel": "email", "subject": "...", "body": "...", "delay_days": 0}, ...]}"""

    def _build_prompt(self, data: dict) -> str:
        lead = data.get("lead", {})
        playbook = data.get("playbook_context", "")
        return f"Playbook:\n{playbook}\n\nLead:\n{lead}\n\nGenerate a 3-step outreach sequence. Return JSON."

    def _max_tokens(self) -> int:
        return 4096


class ReplyClassifierAgent(BaseAgent):
    """Classifies inbound replies: intent + routing decision."""

    agent_type = "reply_classifier"

    def _system_prompt(self) -> str:
        return """You classify B2B outbound replies.
Intents: "interested", "objection", "not_now", "not_a_fit", "out_of_office", "unknown".
Routing: null (auto-handle), "closer" (escalate to human).
INTERESTED always routes to "closer".
Return JSON: {"intent": "...", "confidence": 0.0-1.0, "routed_to": null|"closer", "summary": "..."}"""

    def _build_prompt(self, data: dict) -> str:
        reply = data.get("reply_body", "")
        return f"Classify this reply:\n\n{reply}\n\nReturn JSON."

    def _temperature(self) -> float:
        return 0.0  # maximum determinism for classification


class ReplyDrafterAgent(BaseAgent):
    """Drafts replies to objections or handles auto-responses."""

    agent_type = "reply_drafter"

    def _system_prompt(self) -> str:
        return """You draft replies to B2B outbound responses.
Use the playbook context for objection handling.
For "not_now" → soft nurture. For "objection" → address with proof points.
For "interested" → escalate to closer (do NOT draft — just mark routed_to="closer").
Return JSON: {"subject": "...", "body": "...", "routed_to": null|"closer"}"""

    def _build_prompt(self, data: dict) -> str:
        intent = data.get("intent", "unknown")
        reply_body = data.get("reply_body", "")
        playbook = data.get("playbook_context", "")
        return f"Intent: {intent}\nReply: {reply_body}\nPlaybook:\n{playbook}\n\nDraft response. Return JSON."
