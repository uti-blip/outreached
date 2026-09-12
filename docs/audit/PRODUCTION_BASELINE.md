# Audit de production — Outreached

Date : 12 septembre 2026. Base Git initiale : `986fafb`. L’audit inclut les modifications locales préexistantes du workspace Lexia, préservées puis finalisées.

## Verdict en cours de validation

Le périmètre livré est l’espace privé de prospection manuelle. La publication de l’API n’est pas encore effectuée : Railway est connecté, mais son workspace refuse la création du projet avec « Your trial has expired ». Vercel est accessible via le CLI. Aucun succès de production n’est revendiqué sur la seule base des tests locaux.

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

- 153 tests Python réussis, 1 test Redis optionnel ignoré (avant ajout des tests du smoke HTTP).
- 13 tests frontend réussis ; lint, TypeScript et build de production réussis.
- Audits des dépendances de production : zéro vulnérabilité connue après correction.
- Preuves de configuration : mauvais secrets/hôtes/origines refusés ; configuration correcte démarrée ; envoi live refusé.
- Parcours HTTP sur base synthétique isolée : connexion/CSRF, profil, création et déduplication d’un prospect, trois brouillons, préparation sans envoi, déclaration manuelle distincte d’une délivrance, refus du doublon et d’une relance prématurée, opposition persistante, exports, déconnexion.
- Connexion et tableau de bord vérifiés dans Chrome. Aucun message réel envoyé.

Ces tests ne prouvent pas encore le déploiement Docker distant, la persistance d’un volume Railway ou la disponibilité de l’API publique. Les résultats CI et déploiement doivent être ajoutés après exécution.

## Limites explicites

L’espace est mono-utilisateur. Aucun paiement, enrichissement Apollo, génération IA distante, envoi automatique, suivi d’ouverture ou webhook fournisseur n’est activé. Ces fonctions exigent des intégrations réelles, une persistance des messages, idempotence et protections métier avant activation. Un booléen commercial ne suffit pas à rendre les stubs utilisables.

La limitation de connexion locale est propre à une instance ; une règle d’entrée hébergeur doit la compléter. Le healthcheck frontend vérifie la vie du processus, tandis que le smoke authentifié vérifie sa configuration et la liaison API. La haute disponibilité et le multi-tenant restent hors du périmètre existant.

Déploiement, sauvegardes et retour arrière : [DEPLOYMENT.md](../DEPLOYMENT.md).
