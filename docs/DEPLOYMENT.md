# Déploiement Outreached

## Hébergement gratuit

Le déploiement utilise un **workspace Render Hobby sans carte bancaire**, un seul service **Free** à Francfort et une base **Neon Free** à Francfort. Aucun disque, base Render, worker, domaine payant ou abonnement n’est nécessaire.

`deploy/Dockerfile.free` construit Next.js en mode standalone et FastAPI. Le conteneur tourne sans privilèges. Next.js écoute le port public fourni par Render ; FastAPI reste sur `127.0.0.1:8001`, protégé par sa clé. Les données résident exclusivement dans PostgreSQL. Si un processus échoue, le superviseur arrête l’autre pour que Render constate l’échec.

Limites vérifiées le 12 septembre 2026 :

| Service | Quota gratuit / comportement |
|---|---|
| Render | 750 heures d’instance, 500 minutes de build et 5 Go de trafic par workspace/mois |
| Render au repos | Veille après 15 minutes, réveil d’environ une minute ; redémarrages possibles |
| Neon | 0,5 Go, 100 CU-heures et 5 Go de trafic par projet/mois ; calcul arrêté au repos |
| Dépassements | Sans carte Render et avec Neon Free, interruption possible ; ne pas activer un plan payant |

Render déconseille explicitement ses instances gratuites pour les applications exigeant une disponibilité de production. Cette mise en ligne gratuite sert un espace manuel à faible trafic, avec ces limites. Aucun mécanisme de ping artificiel ne maintient les services éveillés.

Références : [Render gratuit](https://render.com/docs/free), [facturation Render](https://render.com/docs/faq#billing), [Neon Free](https://neon.com/pricing). Vercel Hobby n’est pas retenu pour cet usage commercial.

## Préparer la base privée

Créer une base dédiée, distincte de Lexia. Le workspace utilise le schéma `outreached_workspace`, jamais les tables publiques du prototype. La migration `004_workspace.sql` est appliquée par le script ci-dessous ; ne pas exécuter `001-003`.

Charger les secrets dans l’environnement depuis un fichier local en 0600, exclu de Git. Ne pas mettre d’URL de connexion ni de mot de passe dans les arguments de commande, les logs ou une image.

```bash
# WORKSPACE_MIGRATION_DATABASE_URL : connexion propriétaire, sslmode=verify-full
# WORKSPACE_RUNTIME_DB_PASSWORD : nouveau secret aléatoire d’au moins 32 caractères
uv run python -m scripts.workspace_postgres migrate
```

Le script crée le rôle `outreached_app` sans droits d’administration, le schéma et les tables. L’application ne peut pas créer/modifier le schéma ni supprimer des tables. La vérification TLS du certificat et du nom d’hôte est obligatoire pour toute connexion distante. Conserver séparément la connexion propriétaire ; seule la connexion du rôle restreint va dans Render.

Les écritures prennent un verrou transactionnel PostgreSQL pour protéger la déduplication et les déclarations d’envoi concurrentes. Les lectures/exportations utilisent un instantané cohérent. Le pool est limité à deux connexions par processus.

## Identifiants et configuration Render

Après avoir identifié le domaine HTTPS réellement attribué au service :

```bash
node frontend/scripts/setup-auth.mjs --output .runtime/production \
  --user admin --origin https://votre-service.onrender.com --internal
```

Le script crée des fichiers privés en 0600 et refuse l’écrasement. Conserver `workspace-login.txt` dans un gestionnaire de mots de passe. Importer les variables de `frontend.env` dans Render ; aucune n’utilise `NEXT_PUBLIC_`.

| Variable | Valeur |
|---|---|
| `WORKSPACE_DATABASE_URL` | Connexion du rôle `outreached_app`, `sslmode=verify-full` |
| `WORKSPACE_PUBLIC_ORIGIN` | Domaine HTTPS exact du service |
| `WORKSPACE_LOGIN_USER`, `WORKSPACE_PASSWORD_HASH` | Générés par le script |
| `WORKSPACE_SESSION_SECRET`, `WORKSPACE_API_KEY`, `SECRET_KEY` | Secrets distincts générés |
| `BACKEND_INTERNAL` | `true` |
| `BACKEND_URL` | `http://127.0.0.1:8001` exactement |
| `COMMERCIAL_LAUNCH_ENABLED` | `false` |

`render.yaml` fixe le plan `free`, le Dockerfile combiné, une seule instance et `/health`. Le lanceur fixe production, hôtes et origines à partir du domaine public. Les clés des fournisseurs restent absentes. Aucun envoi automatique n’est activé.

## Publication et vérification

Faire réussir la CI avant de publier la révision sur le service. Vérifier que le service est bien `Free` et que le workspace n’a aucun moyen de paiement. Ne pas utiliser de base Render gratuite : elle expire après 30 jours.

Le `/health` public doit répondre `200 {"status":"ready"}` seulement lorsque la configuration de connexion et le stockage de l’API privée sont disponibles. La vérification complète utilise aussi une session :

```bash
# WORKSPACE_LOGIN_USER et WORKSPACE_LOGIN_PASSWORD dans l’environnement
uv run python scripts/smoke_deployment.py \
  --frontend-url https://votre-service.onrender.com --combined
```

Ce contrôle vérifie disponibilité, accès anonyme refusé, connexion, cookie sécurisé, lecture du workspace, refus d’écriture sans CSRF et déconnexion. Il ne modifie aucune donnée métier. Après une veille prolongée, attendre la fin du réveil avant de le relancer.

Le parcours métier synthétique complet et la persistance après redémarrage sont testés dans la CI sur une base isolée. Ne pas créer de faux prospects ou faux envois dans l’espace réel pour effectuer un smoke. Les secrets ne doivent pas apparaître dans les journaux.

## Migration et sauvegarde

Avant toute migration d’une base locale existante, en faire une sauvegarde cohérente. Les données locales ne sont pas copiées automatiquement vers Internet.

```bash
uv run python scripts/workspace_backup.py backup backend/lexia.db .runtime/local-backup.db
# Cible PostgreSQL vide, connexion propriétaire dans WORKSPACE_MIGRATION_DATABASE_URL
uv run python -m scripts.workspace_postgres restore .runtime/local-backup.db
```

Les identifiants d’origine, brouillons, événements et oppositions sont conservés. Une cible contenant déjà des données est refusée, et une restauration échouée est annulée entièrement.

Pour une sauvegarde portable du workspace PostgreSQL, charger la connexion restreinte dans `WORKSPACE_DATABASE_URL`, puis :

```bash
uv run python -m scripts.workspace_postgres backup .runtime/workspace-backup.json
```

Le fichier est créé en 0600 sans écrasement, sur un instantané cohérent. Le conserver chiffré hors de la base et vérifier sa restauration dans une base dédiée vide. La restauration PostgreSQL demande les identifiants propriétaire. Ne jamais déposer une sauvegarde contenant des prospects dans Git ou un artefact public.

Le projet Neon gratuit affiche un historique de restauration de six heures. Ce délai court ne remplace pas une copie externe avant une opération importante. Vérifier la fenêtre affichée dans Neon avant de compter sur une restauration temporelle.

## Exploitation et retour arrière

En cas de 5xx persistants, accès anonyme ou perte de persistance, fermer le trafic et rétablir une release déjà vérifiée avec authentification. Ne pas revenir à l’ancienne interface sans login. Un rollback de code ne doit pas restaurer les données sans diagnostic.

Changer le hash de mot de passe ou le secret de session révoque les sessions. Coordonner toute rotation de clé API. La limitation de connexion est locale au processus ; le service gratuit utilise une seule instance. Elle est remise à zéro à chaque redémarrage. La haute disponibilité et le multi-utilisateur restent hors du périmètre.

## Alternative SQLite avec volume

Le `Dockerfile` historique et `scripts/serve.py` restent adaptés à un hébergeur avec un vrai volume `/data`. Ils refusent un stockage éphémère, puis abandonnent root avant de démarrer l’API. `WORKSPACE_DB_PATH=/data/workspace.db`, une réplique et un worker sont requis ; `WORKSPACE_DATABASE_URL` doit rester vide. Cette alternative n’est pas le déploiement gratuit décrit ci-dessus.
