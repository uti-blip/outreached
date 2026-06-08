# CLAUDE.md — outreached (ex-hermes-outreach)

> Build conventions, stack, commands, and guardrails for Claude Code agents.

## Stack

- **Backend:** FastAPI (Python 3.12, `uv`), Celery + Redis (fan-out), Supabase (Postgres + pgvector + RLS)
- **Frontend:** None in Phase 1. Scaffold Next.js 14 in Phase 2 only.
- **Inférence:** routing tiéré — `deepseek-v4-flash` (volume) / Kimi K2.6 (intermédiaire) / Claude Sonnet (complexe)
- **Déploiement:** Railway (back), Docker local (Redis, Supabase CLI pour dev)
- **Package manager:** `uv` (pip replacement + venv)

## Project Structure

```
outreached/
├── backend/
│   └── app/
│       ├── main.py              # FastAPI app factory
│       ├── config.py            # Pydantic Settings (env-driven)
│       ├── celery_app.py        # Celery app + config
│       ├── db/
│       │   └── supabase.py      # Supabase client singleton
│       ├── llm/
│       │   ├── provider.py      # LLMProvider abstraction
│       │   └── router.py        # Tiered routing logic
│       ├── agents/
│       │   ├── base.py          # BaseAgent
│       │   ├── sourcing.py
│       │   ├── enrichment.py
│       │   ├── icp_scoring.py
│       │   ├── sequence_writer.py
│       │   ├── reply_classifier.py
│       │   └── reply_drafter.py
│       ├── rag/
│       │   ├── playbook_store.py   # pgvector retrieval
│       │   └── seed_saas_fr.py     # Seed data for vertical #1
│       ├── adapters/
│       │   ├── base.py             # Abstract interfaces
│       │   ├── email_smartlead.py
│       │   ├── enrichment_apollo.py
│       │   ├── linkedin_unipile.py
│       │   └── mocks.py            # Injectable mocks for testing
│       ├── campaign_runner.py      # Pipeline orchestrator
│       └── cost_tracker.py         # agent_runs logger (async)
├── migrations/                    # Supabase SQL migrations
├── tests/
├── docker-compose.yml             # Redis + Supabase local
├── pyproject.toml
├── .env.example
└── README.md
```

## Commands

```bash
# Setup
uv sync                      # Install all deps
uv sync --group dev          # + dev deps
cp .env.example .env         # Then edit .env with real keys

# Dev
uv run uvicorn backend.app.main:app --reload --port 8000
uv run celery -A backend.app.celery_app worker --loglevel=info

# Infra (Docker)
docker compose up -d         # Redis + Supabase

# Lint & format
uv run ruff check .
uv run ruff format .

# Tests
uv run pytest
uv run pytest -xvs tests/

# Smoke test (Phase 1 end-to-end)
uv run python -m backend.app.cli run-campaign --seed-list tests/fixtures/seed_saas_fr.json --dry-run
```

## Guardrails (never violate)

1. **DRY-RUN par défaut.** Jamais d'envoi email/LinkedIn réel en dev/test. L'adapter Smartlead reçoit `dry_run=True` par défaut.
2. **Jamais de secret commité.** `.env` est dans `.gitignore`. `.env.example` documente les vars sans valeurs réelles.
3. **Scope Phase 1 uniquement.** Toute tentation de build Phase 2 (frontend, Stripe metering, multi-tenant self-serve) → écrire dans `BACKLOG_PHASE_2.md`, ne pas coder.
4. **Nom de code `Hermès` interdit en surface publique.** Utiliser `outreached` partout dans le code visible (noms de modules, config, logs). `Hermès` est interne seulement.
5. **Dépréciation DeepSeek :** `deepseek-reasoner` / `deepseek-chat` meurent le 2026-07-24. Utiliser exclusivement `deepseek-v4-pro` et `deepseek-v4-flash`.
6. **CNIL B2B :** chaque séquence générée doit contenir lien d'opt-out + identité émetteur.
7. **Aucun "done" sans typecheck + lint + tests au vert.**

## Git

- Branches: `cluster/N-short-desc` par work package
- Commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`
- Petits, atomiques

## Architecte-Orchestrateur

L'essaim de dev est piloté par un Architecte (moi) qui :
- Décompose en work packages
- Délègue aux sub-agents spécialisés
- Sérialise entre couches, parallélise dans les couches
- Vérifie chaque work package avant merge
- Maintient la cohérence cross-package
