# Outreached — B2B Outbound Orchestration (Phase 1)

> **Phase 1: Agency-first foundation.** Playbook vertical + orchestration IA + dry-run pipeline.
> Phase 2 (SaaS multi-tenant) → `BACKLOG_PHASE_2.md`

## Quick Start

```bash
# 1. Clone & setup
git clone <repo-url> outreached && cd outreached
cp .env.example .env   # edit with your API keys

# 2. Install
uv sync --group dev

# 3. Smoke test (mock mode — no API keys needed)
uv run python -m backend.app.cli run-campaign \
    --seed-list tests/fixtures/seed_saas_fr.json

# 4. Run all tests
uv run pytest

# 5. Live mode (requires LLM API keys)
uv run python -m backend.app.cli run-campaign \
    --seed-list tests/fixtures/seed_saas_fr.json --live
```

## Stack

| Layer | Tech | Notes |
|-------|------|-------|
| Backend API | FastAPI (Python 3.12) | `uv` package manager |
| Orchestration | Celery + Redis | Fan-out agents |
| Database | Supabase (Postgres + pgvector) | Region EU, RLS on all tables |
| LLM Inference | DeepSeek v4-flash / Kimi K2.6 / Claude Sonnet | Tiered routing |
| Email | Smartlead API | Dry-run by default |
| Enrichment | Apollo API | Mock in tests |
| LinkedIn | Unipile API | Mock in tests |
| Observability | Langfuse | Branché sur `agent_runs` |

## Architecture

```
seed_list.json → CLI (run-campaign)
                    │
                    ├─ Sourcing (mock/Live DeepSeek flash)
                    ├─ Enrichment (mock/Live)
                    ├─ ICP Scoring (mock/Live Kimi K2.6)
                    ├─ Sequence Writer (mock/Live + RAG playbook)
                    ├─ Send (Smartlead/LinkedIn, DRY-RUN)
                    └─ Reply Classifier (mock/Live)
                         │
                         └─ agent_runs (cost tracking)
```

## Run Tests

```bash
uv run pytest                     # All tests (mock, no API keys)
uv run pytest -k "not celery"    # Skip Redis-dependent test
```

## Environment Variables

See `.env.example`. Required for live mode:
- `DEEPSEEK_API_KEY`
- `KIMI_API_KEY`
- `ANTHROPIC_API_KEY`

Optional (adapters):
- `SMARTLEAD_API_KEY`
- `APOLLO_API_KEY`
- `UNIPILE_API_KEY`

## Guardrails

- **DRY-RUN by default** — never a real email/LinkedIn send in dev/test
- **No secrets in repo** — `.env` is gitignored
- **CNIL B2B compliant** — every sequence includes opt-out link + sender identity
- **RLS isolation** — all Supabase tables are tenant-isolated
- **Scope: Phase 1 only** — Phase 2 backlog in `BACKLOG_PHASE_2.md`

## Project Structure

```
outreached/
├── backend/app/
│   ├── agents/         # 6 AI agent roles
│   ├── adapters/       # Smartlead, Apollo, Unipile (thin + mocks)
│   ├── llm/            # Provider abstraction + tiered router
│   ├── rag/            # Playbook store (pgvector) + saas_fr seed
│   ├── db/             # Supabase client + SQLite fallback
│   ├── main.py         # FastAPI app
│   ├── celery_app.py   # Celery worker config
│   ├── campaign_runner.py       # Live pipeline
│   ├── campaign_runner_mock.py  # Mock pipeline (smoke test)
│   ├── cost_tracker.py # agent_runs logger
│   └── cli.py          # Typer CLI
├── migrations/         # Supabase SQL (001-003)
├── tests/
│   ├── fixtures/       # seed_saas_fr.json
│   └── test_*.py      # 31 tests, 30 pass, 1 skip (Redis)
├── CLAUDE.md           # Dev conventions + guardrails
├── pyproject.toml      # uv + ruff + pytest config
└── .env.example        # Documented env vars
```
