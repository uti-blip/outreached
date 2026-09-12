# Outreached — espace de prospection Lexia

Application privée pour gérer des prospects B2B, préparer des séquences et suivre les envois déclarés. Un utilisateur, un espace : SQLite en local, PostgreSQL persistant pour l’hébergement gratuit.

## Fonctionnement

- Fiches prospects, import CSV, déduplication par email/SIREN et recherche dans l’Annuaire des Entreprises.
- Brouillons personnalisables, préparation dans votre messagerie et relances manuelles.
- Oppositions persistantes, historique et exports CSV/JSON.
- Connexion par identifiant/mot de passe, session signée HttpOnly et protection CSRF.
- Aucune clé de service dans le navigateur ; aucune IA payante nécessaire.

Un envoi déclaré ne prouve ni délivrance ni lecture. Les réponses et rendez-vous sont renseignés manuellement. L’ancienne orchestration IA reste expérimentale : le mode démo est sans réseau ni écriture ; les transports automatiques incomplets refusent les envois réels, même avec des clés configurées.

## Démarrage local

Prérequis : Python 3.12+, uv, Node.js 22 et pnpm 10.33.0.

```bash
uv sync --frozen --group dev
pnpm --dir frontend install --frozen-lockfile
./scripts/dev.sh
```

Ouvrir http://localhost:3000. Le premier démarrage crée les identifiants dans `.runtime/auth/workspace-login.txt` et une configuration privée dans le même dossier, exclus de Git. La base locale existante `backend/lexia.db` est conservée. Aucun compte fournisseur n’est nécessaire.

```bash
# Prévisualisation déterministe sans appel IA ni envoi
uv run python -m backend.app.cli run-campaign \
  --seed-list tests/fixtures/seed_saas_fr.json --dry-run
```

## Validation

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/prove_production_gates.py
pnpm --dir frontend lint
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

La CI reproduit ces contrôles et teste les deux stockages, le contrôle d’accès, la sauvegarde/restauration et la persistance après redémarrage des conteneurs. Le test Redis est optionnel : Redis/Celery ne sont pas requis par le workspace.

## Production

L'application est en ligne sur **[outreached.netlify.app](https://outreached.netlify.app)**, avec **Netlify Free + Neon Free**. Next.js sert l'interface et l'API directement avec `WORKSPACE_BACKEND=postgres` ; PostgreSQL conserve les données entre les déploiements. Le smoke authentifié de production a réussi le 12 septembre 2026 : connexion, stockage, protection CSRF et déconnexion.

Netlify suspend les projets lorsque les 300 crédits mensuels sont épuisés. Le compte est Free, sans carte et sans recharge automatique. La base Neon est à Francfort ; les fonctions Netlify Free tournent en Ohio. Cette configuration ne promet ni une disponibilité continue, ni un traitement exclusivement européen.

Le `Dockerfile` historique reste disponible pour une installation SQLite avec un vrai volume `/data`. Aucune base locale n’est copiée dans les images.

Voir [le guide de déploiement et restauration](docs/DEPLOYMENT.md), [le rapport d’audit](docs/audit/PRODUCTION_BASELINE.md), `.env.example` et `frontend/.env.example`.

Les migrations `migrations/001-003` concernent l’ancien prototype Supabase. Le workspace PostgreSQL utilise uniquement `migrations/004_workspace.sql`, appliqué par `scripts/workspace_postgres.py` dans son schéma privé. Ne pas appliquer les anciennes migrations à une autre application.

## Organisation

| Chemin | Responsabilité |
|---|---|
| `backend/app/prospecting.py` | Prospection, séquences manuelles, exports |
| `backend/app/workspace_store.py` | Stockage du workspace |
| `frontend/src/app` | Interface, connexion, proxy authentifié |
| `backend/app/campaign_runner.py` | Prévisualisation de l’ancien pipeline |
| `scripts/workspace_backup.py` | Sauvegarde/restauration SQLite vérifiée |
| `scripts/workspace_postgres.py` | Migration privée, sauvegarde portable, restauration sans écrasement |
| `netlify.toml` | Publication Next.js et API PostgreSQL sur Netlify Free |
| `deploy/Dockerfile.free` / `render.yaml` | Alternative conteneur Next.js/FastAPI |
| `tests` / `frontend/tests` | Régressions métier et sécurité |

Le SaaS multi-utilisateur reste dans `BACKLOG_PHASE_2.md`.
