"""Tests: Orchestration IA (Cluster 3) — Router, Agents, RAG.

Uses mock LLM provider for deterministic tests (no API keys needed).
"""

from unittest.mock import patch

import pytest

from backend.app.agents.base import (
    ICPScoringAgent,
    ReplyClassifierAgent,
    SequenceWriterAgent,
)
from backend.app.db.supabase import init_db
from backend.app.llm.provider import ProviderKind
from backend.app.llm.router import (
    AGENT_COMPLEXITY,
    COMPLEXITY_PROVIDER,
    InferenceRouter,
    TaskComplexity,
)
from backend.app.rag.playbook_store import PlaybookStore
from backend.app.rag.seed_saas_fr import seed_saas_fr

# ── Fixtures ───────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_db():
    """Ensure SQLite tables exist before each test."""
    init_db()


@pytest.fixture
def mock_complete():
    """Mock LLMProvider.complete to return controlled responses."""
    with patch("backend.app.agents.base.BaseAgent.run") as mock:
        yield mock


# ── Router tests ───────────────────────────────────────


class TestInferenceRouter:
    def test_agent_complexity_mapping(self):
        """Each agent type maps to a valid complexity tier."""
        for _agent_type, complexity in AGENT_COMPLEXITY.items():
            assert complexity in TaskComplexity
            assert COMPLEXITY_PROVIDER[complexity] in ProviderKind

    @patch("backend.app.llm.router.settings")
    def test_router_creation_no_keys(self, mock_settings):
        """Router raises when no providers are configured."""
        mock_settings.deepseek_api_key = ""
        mock_settings.kimi_api_key = ""
        mock_settings.anthropic_api_key = ""

        router = InferenceRouter()
        with pytest.raises(ValueError, match="No LLM providers"):
            router.get_provider("sourcing")

    @patch("backend.app.llm.router.settings")
    def test_router_with_deepseek_only(self, mock_settings):
        """With only DeepSeek key, all agents fall back to DeepSeek."""
        mock_settings.deepseek_api_key = "sk-test"
        mock_settings.kimi_api_key = ""
        mock_settings.anthropic_api_key = ""

        router = InferenceRouter()
        provider = router.get_provider("sourcing")
        assert provider.kind == ProviderKind.DEEPSEEK
        assert "deepseek" in provider.model

        # Reply drafter should fall back to DeepSeek even though it wants Claude
        provider = router.get_provider("reply_drafter")
        assert provider.kind == ProviderKind.DEEPSEEK

    @patch("backend.app.llm.router.settings")
    def test_router_prefers_right_provider(self, mock_settings):
        """With all keys, each agent gets its preferred provider."""
        mock_settings.deepseek_api_key = "sk-test"
        mock_settings.kimi_api_key = "sk-test"
        mock_settings.anthropic_api_key = "sk-test"

        router = InferenceRouter()

        # Volume tasks → DeepSeek
        assert router.get_provider("enrichment").kind == ProviderKind.DEEPSEEK
        assert router.get_provider("reply_classifier").kind == ProviderKind.DEEPSEEK

        # Standard tasks → Kimi
        assert router.get_provider("icp_scoring").kind == ProviderKind.KIMI
        assert router.get_provider("sequence_writer").kind == ProviderKind.KIMI

        # Complex tasks → Anthropic
        assert router.get_provider("reply_drafter").kind == ProviderKind.ANTHROPIC

    @patch("backend.app.llm.router.settings")
    def test_cost_eur_proportional(self, mock_settings):
        """Costs increase with model tier: flash < kimi < sonnet."""
        mock_settings.deepseek_api_key = "sk-test"
        mock_settings.kimi_api_key = "sk-test"
        mock_settings.anthropic_api_key = "sk-test"

        router = InferenceRouter()
        tokens = (500, 200)  # input, output

        cost_deepseek = router.cost_eur("enrichment", *tokens)
        cost_kimi = router.cost_eur("icp_scoring", *tokens)
        cost_sonnet = router.cost_eur("reply_drafter", *tokens)

        assert cost_deepseek < cost_kimi < cost_sonnet


# ── Agent tests (deterministic via mock) ───────────────


class TestICPScoring:
    def test_build_prompt_includes_lead_data(self):
        agent = ICPScoringAgent()
        prompt = agent._build_prompt(
            {
                "enriched": {"name": "Acme Corp", "size": "50-200"},
                "playbook_context": "ICP: SaaS B2B France, Series A+",
            }
        )
        assert "Acme Corp" in prompt
        assert "playbook_context" not in prompt  # injected as content
        assert "ICP" in prompt

    def test_parse_response_valid_json(self):
        agent = ICPScoringAgent()
        result = agent._parse_response(
            '{"score": 75, "breakdown": {"fit": 40, "signals": 20, "timing": 15}, "verdict": "go", "reasoning": "Strong fit"}'
        )
        assert result["score"] == 75
        assert result["verdict"] == "go"

    def test_parse_response_markdown_wrapped(self):
        agent = ICPScoringAgent()
        result = agent._parse_response('```json\n{"score": 60, "verdict": "go"}\n```')
        assert result["score"] == 60

    def test_system_prompt_includes_scoring_rubric(self):
        agent = ICPScoringAgent()
        prompt = agent._system_prompt()
        assert "score" in prompt.lower()
        assert "0-50" in prompt


class TestReplyClassifier:
    def test_build_prompt_includes_reply(self):
        agent = ReplyClassifierAgent()
        prompt = agent._build_prompt(
            {
                "reply_body": "Bonjour, je suis intéressé par votre solution. Pouvez-vous m'en dire plus ?"
            }
        )
        assert "intéressé" in prompt

    def test_parse_response_handles_intents(self):
        agent = ReplyClassifierAgent()
        result = agent._parse_response(
            '{"intent": "interested", "confidence": 0.95, "routed_to": "closer", "summary": "Hot lead"}'
        )
        assert result["intent"] == "interested"
        assert result["routed_to"] == "closer"

    def test_temperature_is_zero(self):
        """Classification must be deterministic."""
        agent = ReplyClassifierAgent()
        assert agent._temperature() == 0.0


class TestSequenceWriter:
    def test_build_prompt_includes_cnil_compliance(self):
        """Sequences must include opt-out + sender identity (CNIL B2B)."""
        agent = SequenceWriterAgent()
        system = agent._system_prompt().lower()
        assert "opt-out" in system
        assert "sender identity" in system

    def test_max_tokens_extra(self):
        """Sequence generation needs more tokens."""
        agent = SequenceWriterAgent()
        assert agent._max_tokens() >= 4096


# ── RAG tests ──────────────────────────────────────────


class TestPlaybookStore:
    @pytest.fixture(autouse=True)
    def _setup_playbook(self):
        """Create playbook FK entries so chunks can be inserted."""
        init_db()
        from backend.app.db.supabase import _ensure_dev_tenant, _get_sqlite

        tenant_id, _ = _ensure_dev_tenant()
        conn = _get_sqlite()
        conn.execute(
            "INSERT OR IGNORE INTO playbooks (id, tenant_id, name, vertical) VALUES (?, ?, ?, ?)",
            ("pb-1", tenant_id, "test-playbook", "saas_fr"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO playbooks (id, tenant_id, name, vertical) VALUES (?, ?, ?, ?)",
            ("pb-test", tenant_id, "seed-playbook", "saas_fr"),
        )
        conn.commit()
        conn.close()

    def test_add_and_search(self):
        """Add a chunk, then find it via keyword search."""
        init_db()  # ensure tables
        store = PlaybookStore()
        store.add_chunk(
            "pb-1", "objection", "Le budget est trop élevé pour nous.", {"tags": ["budget"]}
        )

        results = store.search("budget")
        assert len(results) >= 1
        assert any("budget" in r["content"] for r in results)

    def test_search_by_type(self):
        """Filter results by chunk_type."""
        store = PlaybookStore()
        store.add_chunk("pb-1", "objection", "Objection budget.", {"tags": ["budget"]})
        store.add_chunk("pb-1", "proof", "Case study SaaS RH.", {"tags": ["proof"]})

        objections = store.search_by_type("objection", "budget")
        assert len(objections) >= 1
        assert all(r["chunk_type"] == "objection" for r in objections)

    def test_get_context_returns_string(self):
        """get_context concatenates results for prompt injection."""
        store = PlaybookStore()
        store.add_chunk("pb-1", "pricing", "Setup 3000-5000 EUR.", {"tags": ["pricing"]})

        context = store.get_context("Setup")
        assert "3000" in context
        assert "[pricing]" in context

    def test_seed_saas_fr(self):
        """Seed populates all chunk types."""
        store = PlaybookStore()
        count = seed_saas_fr(store, "pb-test")
        assert count > 10  # 4 objections + 3 sequences + 2 pricing + 4 proof = 13

        # Verify all types present
        for chunk_type in ("objection", "sequence", "pricing", "proof"):
            results = store.search_by_type(chunk_type, "")
            assert len(results) > 0, f"No results for {chunk_type}"

    def test_search_objection_by_keyword(self):
        """Search finds relevant objections."""
        store = PlaybookStore()
        seed_saas_fr(store, "pb-test")

        results = store.search("budget", top_k=3)
        assert len(results) >= 1
        assert any("budget" in r["content"].lower() for r in results)

    def test_search_pricing(self):
        store = PlaybookStore()
        seed_saas_fr(store, "pb-test")

        results = store.search("ROI CAC", top_k=3)
        assert len(results) >= 1
