# BACKLOG — Phase 2 (SaaS multi-tenant)

> **Doctrine: ne rien construire avant le revenu.**
> Ce backlog n'est activé qu'après validation des playbooks par au moins 3 clients agency payants.

## Prérequis (gates)

- [ ] ≥ 3 clients agency récurrents à ≥1 500 €/mois
- [ ] ≥ 1 playbook vertical validé (sequences + objections + pricing en production)
- [ ] Smoke test Phase 1 vert, `agent_runs` loggé, marge brute >85%

## L6 — Frontend Next.js 14

- Scaffold Next.js 14 App Router + TypeScript + Tailwind + Shadcn UI
- Supabase Auth (magic link + OAuth Google)
- Pages: Campaign builder, Lead inbox, Reply inbox, Analytics, Playbook editor
- Vercel deploy
- **NON-GOAL:** dashboard analytics complet (itérer en fonction des retours clients)

## L9 — SaaS Multi-Tenant

- RLS multi-tenant avec Supabase Auth (JWT → `tenant_id` claim)
- Self-serve onboarding (Stripe Checkout → tenant provisioning)
- Isolation stricte: un tenant ne voit JAMAIS les données d'un autre

## L10 — Stripe Billing

- Stripe Subscriptions (tiers 99/299/499 €/mois)
- Stripe Meters: usage-based billing (0,02 €/agent-run au-delà du quota)
- Facturation électronique FR: brancher Plateforme Agréée (Pennylane/Qonto)
- Dashboard billing/client: MRR, usage, marge

## L11 — Observabilité LLM

- Langfuse dashboard: coût par agent, latence, qualité des réponses
- Alerting: dérive de scoring ICP, taux de réponse anormal
- Dashboard marge/client (coût agent-runs vs MRR du tenant)

## L12 — Délivrabilité Email

- Warmup automatisé des mailboxes (Smartlead)
- Rotation de domaines + monitoring inbox placement
- SPF/DKIM/DMARC provisioning automatisé
- Resend pour le transactionnel produit (pas de cold)

## L13 — Templates Verticaux

- Chaque nouveau vertical = config YAML, pas re-build
- Playbook editor UI (drag-and-drop sequences)
- Import/export de playbooks entre tenants
- Marketplace de playbooks (optionnel)

## L14 — Scale & Production Hardening

- Celery fan-out à l'échelle (100-300 agents parallèles)
- Redis rate-limiting des appels LLM + API externes
- Cache d'enrichissement (ne pas re-payer un lookup déjà fait)
- WebSocket/SSE pour flux de réponses temps réel
- CI/CD: GitHub Actions → Railway (back) + Vercel (front)

## L15 — Compliance SaaS

- DPA (Data Processing Agreement) avec chaque client
- Registre des traitements (Art. 30 RGPD)
- Gestion des sous-traitants ultérieurs (LLM providers, Smartlead, Unipile, Supabase)
- Audit trail AI Act (Art. 12) → bridge optionnel vers ARRtist

## Risques & Décisions Ouvertes

| # | Sujet | Statut |
|---|-------|--------|
| 1 | **Marque publique** — conflit Hermès International. Outreached est le nom actuel, à valider INPI. | À trancher |
| 2 | **DeepSeek v4 migration** — deadline 2026-07-24. `deepseek-v4-pro`/`v4-flash` utilisés, OK. | ✓ |
| 3 | **Cold email warmup** — 2-3 semaines avant premier envoi sérieux. À lancer en Week 0. | En attente |
| 4 | **Facturation électronique** — réception obligatoire 1er sept 2026. Brancher Plateforme Agréée. | À faire |
| 5 | **LinkedIn ToS** — Unipile sous le radar. Risque permanent, canal secondaire à l'email. | Accepté |
