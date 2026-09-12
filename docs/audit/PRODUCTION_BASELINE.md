# Audit de production — Outreached

Date : 12 septembre 2026. Base Git initiale : `986fafb`. L’audit inclut les modifications locales préexistantes du workspace Lexia, préservées puis finalisées.

## Verdict en cours de validation

Le périmètre livré est l’espace privé de prospection manuelle. L’audit initial est fusionné dans la [PR #1](https://github.com/uti-blip/outreached/pull/1). À la demande de l’utilisateur, la cible devient **Netlify Free + Neon Free**, sans Railway ni abonnement. Le port des routes métier dans Next.js est en cours de validation ; une production opérationnelle ne sera déclarée qu’après contrôle du déploiement public.

## Constats et corrections

| Priorité | Défaut confirmé | Correction / preuve |
|---|---|---|
| P0 | Proxy public injectant la clé API sans authentifier l’utilisateur | Connexion, session signée HttpOnly, contrôle d’origine, CSRF et liste de routes autorisées ; tests frontend |
| P0 | Mode simulation réalisant des appels IA payants et écritures | Prévisualisation déterministe sans réseau/base ; tests interdisant explicitement les appels externes |
| P0 | Faux scores/réponses/succès de transports incomplets | Résultats non inventés et envois live refusés dans chaque point d’entrée |
| P0 | Configuration production contournable par APP_ENV mal orthographié | Valeurs validées, secrets/hôtes/origines contrôlés, debug refusé, chemin DB absolu |
| P0 | Dépendances exposées à des vulnérabilités connues | Next 16.3.5 et dépendances Python corrigées ; audits production sans vulnérabilité connue au moment du contrôle |
| P1 | Stockage local éphémère possible au déploiement | Conteneur exigeant un vrai volume /data, utilisateur sans privilèges, une réplique |
| P1 | Lectures SQLite prenant un verrou d’écriture | Transactions de lecture dédiées ; tests de concurrence et déduplication |
| P1 | Entrées CSV/annuaire invalides pouvant provoquer une erreur serveur | Validation bornée et erreurs contrôlées ; tests de régression |
| P1 | Sauvegarde sans preuve de restauration | Copie SQLite cohérente, vérification d’intégrité/références, refus d’écrasement, restauration testée |
| P1 | Absence de chaîne de validation versionnée | GitHub Actions : backend, frontend et construction/smoke du conteneur |

## Preuves locales

- 176 tests Python réussis, 1 test Redis optionnel ignoré ; 23 tests dédiés au smoke HTTP inclus.
- 13 tests frontend réussis ; lint, TypeScript et build de production réussis.
- Audits des dépendances de production : zéro vulnérabilité connue après correction.
- Preuves de configuration : mauvais secrets/hôtes/origines refusés ; configuration correcte démarrée ; envoi live refusé.
- Parcours HTTP sur base synthétique isolée : connexion/CSRF, profil, création et déduplication d’un prospect, trois brouillons, préparation sans envoi, déclaration manuelle distincte d’une délivrance, refus du doublon et d’une relance prématurée, opposition persistante, exports, déconnexion.
- Connexion et tableau de bord vérifiés dans Chrome. Aucun message réel envoyé.

La [CI GitHub 34674212523](https://github.com/uti-blip/outreached/actions/runs/34674212523) a réussi sur `6e41aa1` : jobs backend, frontend et conteneur. Le job conteneur a construit l’image, démarré l’API en production avec volume Docker, vérifié 401 anonyme / 200 authentifié, sauvegardé/restauré et vérifié la persistance après redémarrage.

Une [prévisualisation Vercel](https://outreached-d091td2jr-uti-blips-projects.vercel.app) a été construite et publiée (`dpl_87t7ELH46mk1ZTHC3TfWcZscURa7`). Elle reste fermée sans identifiants/configuration API. Le champ non supporté `comment` a été retiré de `vercel.json` après validation par le service.

Ces preuves décrivent la première phase d’audit. L’essai Railway expiré a empêché sa publication ; cette dépendance a ensuite été retirée du chemin gratuit. Aucun projet/service Lexia existant n’a été modifié.

## Adaptation à l’hébergement gratuit

- Workspace Render **Outreached**, plan Hobby, **sans carte bancaire**, créé séparément de Lexia.
- Projet Neon **outreached**, plan Free, PostgreSQL 18 à Francfort ; quotas affichés : 0,5 Go, 100 CU-heures et 5 Go de trafic.
- Une image combinée Next.js/FastAPI, API liée uniquement à la boucle locale, utilisateur non privilégié et arrêt coordonné en cas d’échec.
- PostgreSQL externe avec certificat et nom d’hôte vérifiés, schéma privé, rôle applicatif sans administration, transactions protégeant concurrence et exports.
- Sauvegarde portable des six tables, incluant les oppositions, et restauration atomique dans une cible vide.
- Le healthcheck public vérifie maintenant la configuration, l’API interne et son stockage ; un HTTP 200 de simple liveness ne suffit plus.
- 20 tests frontend et 5 tests du superviseur réussis ; lint, TypeScript et build standalone réussis. Validation complète du conteneur PostgreSQL suivie par la CI.

## Passage à Netlify

Render a finalement exigé une carte dans l’API et l’interface même pour créer un service Free. Aucun service n’a été créé. Le compte Netlify existant a été vérifié Free, sans carte, avec 300 crédits et recharge automatique désactivée. Le projet `outreached` a été créé. La base Neon est migrée et la connexion du rôle `outreached_app` vérifiée ; les six tables métier sont vides, comme la base locale.

Les routes manuelles sont portées dans Next.js, avec le même schéma et les mêmes contrats métier. Leur validation HTTP réelle et le déploiement public sont encore requis. Les adaptateurs Python et conteneurs restent disponibles.

## Limites explicites

L’espace est mono-utilisateur. Aucun paiement, enrichissement Apollo, génération IA distante, envoi automatique, suivi d’ouverture ou webhook fournisseur n’est activé. Ces fonctions exigent des intégrations réelles, une persistance des messages, idempotence et protections métier avant activation. Un booléen commercial ne suffit pas à rendre les stubs utilisables.

Le limiteur distribué pour Netlify est en cours de vérification. Netlify Free suspend les projets à épuisement des crédits ; aucun dépassement payant n’est activé. Neon Free conserve les données au repos, avec une fenêtre courte de restauration temporelle à compléter par des copies externes. Cette configuration ne garantit pas une disponibilité continue. La haute disponibilité et le multi-tenant restent hors du périmètre existant.

Déploiement, sauvegardes et retour arrière : [DEPLOYMENT.md](../DEPLOYMENT.md).
