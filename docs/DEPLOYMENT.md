# Déploiement Outreached

## Cible gratuite : Netlify + Neon

Le projet Netlify `outreached` est créé sur le compte Free existant : 300 crédits mensuels, aucun moyen de paiement, recharge automatique désactivée. L'application est destinée à `https://outreached.netlify.app`. **La création du projet ne prouve pas la publication : celle-ci doit être suivie d'un smoke authentifié réussi.**

Next.js sert l'interface et les routes métier. Avec `WORKSPACE_BACKEND=postgres`, les routes accèdent directement à Neon depuis le serveur après vérification de session et CSRF ; aucun service Python public n'est nécessaire. PostgreSQL utilise le même schéma privé et le même rôle restreint que le backend Python.

| Service | Offre et limites vérifiées le 12 septembre 2026 |
|---|---|
| Netlify Free | 300 crédits/mois partagés par les projets du compte ; suspension au plafond, sans dépassement facturé |
| Fonctions Netlify | Démarrage à la demande, 1 Go de mémoire ; région du projet : Ohio (`us-east-2`) |
| Neon Free | 0,5 Go, 100 CU-heures et 5 Go de trafic par projet/mois ; calcul arrêté au repos |
| Localisation des données | Base PostgreSQL à Francfort ; traitement des requêtes Netlify en Ohio, donc pas d'hébergement exclusivement européen |

Les quotas et redémarrages peuvent interrompre l'accès. L'offre gratuite ne garantit pas une disponibilité continue. Ne pas activer un abonnement, une recharge automatique ou un service IA payant pour la maintenir en ligne.

Références : [Netlify Free et quotas](https://www.netlify.com/pricing/pro-vs-free/), [usage commercial autorisé](https://www.netlify.com/blog/introducing-netlify-free-plan/), [régions des fonctions](https://docs.netlify.com/build/functions/configuration/#region), [Neon Free](https://neon.com/pricing).

## Préparer la base privée

Utiliser une base dédiée, distincte de Lexia. Le schéma est `outreached_workspace`. Ne pas exécuter les migrations historiques `001-003` du prototype Supabase.

Charger les secrets depuis un fichier local en 0600, exclu de Git. Ne pas mettre de connexion ou de mot de passe dans les arguments des commandes, les journaux ou les images.

```bash
# WORKSPACE_MIGRATION_DATABASE_URL : propriétaire, sslmode=verify-full
# WORKSPACE_RUNTIME_DB_PASSWORD : secret aléatoire d'au moins 32 caractères
uv run python -m scripts.workspace_postgres migrate
```

Ce script applique `004_workspace.sql`, crée `outreached_app` sans droits d'administration et répare les droits excessifs sur les tables privées. Le rôle applicatif ne peut pas supprimer les tables ni effacer les données métier. Conserver la connexion propriétaire séparément ; seule la connexion restreinte va dans Netlify.

TLS vérifie le certificat et le nom d'hôte. L'URL Node doit utiliser `sslmode=verify-full` sans `channel_binding=require`, une garantie libpq que le pilote Node ne propose pas. Les paramètres qui pourraient remplacer l'hôte ou les options TLS sont refusés.

Les écritures utilisent le même verrou transactionnel que Python, et les lectures un instantané cohérent. Le pool Node est limité à une connexion par instance. La table `login_rate_limit` conserve un état borné partagé entre instances, avec compteurs expirants globaux et identifiants de sources hachés. Elle n'entre pas dans les sauvegardes métier.

## Identifiants et variables Netlify

```bash
node frontend/scripts/setup-auth.mjs --output .runtime/production/netlify \
  --user admin --origin https://outreached.netlify.app --postgres
```

Le script crée des fichiers privés en 0600 et refuse leur écrasement. Placer `workspace-login.txt` dans un gestionnaire de mots de passe. Importer la configuration côté serveur avec le CLI Netlify authentifié, en ajoutant `WORKSPACE_DATABASE_URL` depuis son fichier privé. Aucune variable ne doit utiliser le préfixe `NEXT_PUBLIC_`.

| Variable | Valeur |
|---|---|
| `WORKSPACE_BACKEND` | `postgres` |
| `WORKSPACE_DATABASE_URL` | Connexion du rôle `outreached_app`, TLS vérifié |
| `WORKSPACE_PUBLIC_ORIGIN` | Origine HTTPS exacte du déploiement |
| `WORKSPACE_LOGIN_USER`, `WORKSPACE_PASSWORD_HASH` | Générés par le script |
| `WORKSPACE_SESSION_SECRET` | Secret de session distinct généré |

Le mode PostgreSQL direct ne nécessite ni `BACKEND_URL`, ni `BACKEND_INTERNAL`, ni `WORKSPACE_API_KEY`. Les clés de fournisseurs payants restent absentes. Les routes historiques de simulation et les envois automatiques ne sont pas exposés.

`netlify.toml` fixe le répertoire `frontend`, le build et les versions Node/pnpm. Netlify applique automatiquement son adaptateur Next.js. Les secrets restent dans les variables du projet, jamais dans ce fichier.

## Publication et vérification

Faire réussir les contrôles avant de publier la révision. Vérifier dans Netlify que le compte reste Free et sans recharge automatique. Une prévisualisation utilise sa propre origine HTTPS exacte ; ne pas assouplir le contrôle d'origine pour la tester.

```bash
netlify deploy --context deploy-preview --alias audit
# Après vérifications, publier :
netlify deploy --prod
```

Le `/health` public répond `200 {"status":"ready"}` seulement si les identifiants de connexion et le stockage restreint sont opérationnels. Pour contrôler le déploiement :

```bash
# WORKSPACE_LOGIN_USER et WORKSPACE_LOGIN_PASSWORD dans l'environnement
uv run python scripts/smoke_deployment.py \
  --frontend-url https://outreached.netlify.app --combined
```

`--combined` signifie ici une seule origine publique. Le smoke vérifie disponibilité, refus anonyme, connexion, cookie sécurisé, lecture du workspace, refus d'écriture sans CSRF et déconnexion. Il ne modifie aucune donnée métier.

La CI démarre Next.js seul sur une base PostgreSQL TLS isolée, exécute les contrats métier existants par HTTP, puis vérifie la persistance après redémarrage. Les faux prospects et fausses déclarations d'envoi restent dans ces bases de test, jamais dans l'espace réel.

## Migration et sauvegarde

La base locale a été vérifiée vide avant la première publication. Une base locale existante n'est pas copiée automatiquement vers Internet. Pour une migration ultérieure, commencer par une sauvegarde cohérente :

```bash
uv run python scripts/workspace_backup.py backup backend/lexia.db .runtime/local-backup.db
# Connexion propriétaire dans WORKSPACE_MIGRATION_DATABASE_URL, cible vide :
uv run python -m scripts.workspace_postgres restore .runtime/local-backup.db
```

Les identifiants, brouillons, événements et oppositions sont conservés. Une cible contenant déjà des données est refusée ; une restauration échouée est annulée entièrement.

Pour une sauvegarde portable, charger la connexion restreinte dans `WORKSPACE_DATABASE_URL` :

```bash
uv run python -m scripts.workspace_postgres backup .runtime/workspace-backup.json
```

Le fichier est créé en 0600 sans écrasement. Le conserver chiffré hors de la base et vérifier sa restauration dans une base dédiée vide. Ne jamais publier une sauvegarde de prospects dans Git ou dans un artefact public. L'historique Neon Free affiché à la création est de six heures ; il ne remplace pas une copie externe avant une opération importante.

## Exploitation et retour arrière

En cas d'erreurs persistantes, d'accès anonyme ou de perte de persistance, fermer le trafic et rétablir un déploiement déjà vérifié avec authentification. Ne pas revenir à l'ancienne interface sans login. Un retour arrière du code ne doit pas restaurer les données sans diagnostic.

Changer le hash de mot de passe ou le secret de session révoque les sessions. La limitation de connexion PostgreSQL persiste entre instances et redémarrages. La haute disponibilité et le multi-utilisateur restent hors du périmètre actuel.

## Alternatives conteneurisées

`deploy/Dockerfile.free` reste utilisable pour Next.js et FastAPI réunis, avec PostgreSQL externe. Render Free a été essayé : l'API et l'interface demandent une carte pour créer ce service, même dans le workspace dédié gratuit. Aucun moyen de paiement n'a été ajouté, aucun service Render n'a été créé. Une carte autoriserait des dépassements de trafic facturés ; Render n'est donc pas la cible retenue.

Le `Dockerfile` historique utilise SQLite sur un vrai volume `/data`. Il refuse un stockage éphémère, quitte root avant de lancer l'API et nécessite une seule réplique. Aucun fichier de base local n'est inclus dans les images.
