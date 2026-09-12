# Déploiement Outreached

## Architecture

Un espace privé de prospection manuelle. Next.js authentifie chaque requête API avec une session utilisateur ; seul son serveur détient la clé FastAPI. SQLite reste sur un volume persistant de l’API, avec une seule réplique et un worker. Les envois automatiques sont indisponibles.

## Configuration

1. Utiliser un projet/service **Outreached** distinct de Lexia dans un workspace Railway actif, en région européenne. Construire depuis la racine du dépôt avec son Dockerfile.
2. Attacher un volume à `/data`. Le démarrage refuse un simple répertoire éphémère. Générer le domaine HTTPS de l’API.
3. Réutiliser le projet Vercel `outreached` depuis `frontend`. Identifier son domaine stable. Node.js 22, pnpm 10.33.0, commande `pnpm build`.
4. Générer la configuration privée avec les domaines vérifiés :

```bash
node frontend/scripts/setup-auth.mjs --output .runtime/production \
  --user admin --origin https://app.example.com \
  --backend-url https://api.example.com
```

Le script écrit `frontend.env` et `workspace-login.txt` en 0600 et refuse l’écrasement. Conserver les identifiants dans un gestionnaire de mots de passe. Aucun de ces fichiers dans Git, les logs ou l’image.

5. Importer les variables de `frontend.env` dans Vercel comme variables **serveur** de l’environnement visé. Aucune n’utilise `NEXT_PUBLIC_`. Une preview exige des identifiants et une origine distincts ; sans configuration, l’accès échoue.
6. Définir ces variables Railway :

| Variable | Valeur |
|---|---|
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `COMMERCIAL_LAUNCH_ENABLED` | `false` |
| `WORKSPACE_DB_PATH` | `/data/workspace.db` |
| `WORKSPACE_API_KEY` | valeur du fichier frontend privé |
| `SECRET_KEY` | autre secret aléatoire, au moins 32 caractères |
| `ALLOWED_HOSTS` | JSON des hôtes API + `healthcheck.railway.app` |
| `ALLOWED_ORIGINS` | JSON de l’origine HTTPS exacte du frontend |

Les clés fournisseurs restent absentes. Le conteneur utilise `PORT` fourni par Railway ; il ajuste la propriété du volume puis abandonne root avant de charger l’application.

## Publication et vérification

Exécuter les validations du README et vérifier les trois jobs de `.github/workflows/ci.yml`. `railway up` publie l’API du projet lié. Depuis `frontend`, `pnpm dlx vercel --prod` publie l’interface.

La production est vérifiée seulement après :

- API `/health/ready` en 200, workspace anonyme en 401 ;
- frontend `/health` en 200, page anonyme redirigée vers la connexion ;
- connexion valide, lecture du workspace, écriture sans CSRF refusée et déconnexion ;
- parcours métier complet avec données synthétiques dans un environnement isolé ;
- restauration vérifiée, redémarrage et persistance constatée ;
- consultation des logs et stabilité des contrôles après publication.

`scripts/smoke_deployment.py` effectue les contrôles HTTP sans modifier les données métier. Le `/health` frontend seul prouve seulement la réponse du processus.

## Sauvegarde et restauration

Activer les sauvegardes du volume. En complément, conserver une copie SQLite chiffrée hors du volume. Une copie sur le même disque ne protège pas contre sa perte.

```bash
python scripts/workspace_backup.py backup /data/workspace.db /data/backup-20260912.db
python scripts/workspace_backup.py restore /data/backup-20260912.db /data/restore-check.db
```

Les copies sont cohérentes, y compris avec WAL. Le script vérifie intégrité, références et tables requises, puis refuse tout écrasement. Les tests comparent également les données restaurées. Le JSON fourni par l’interface est un export ; la restauration documentée utilise SQLite.

Pour une restauration réelle : arrêter l’API, préserver la base courante et ses WAL/SHM, restaurer vers un fichier neuf, contrôler les comptes et oppositions, puis remplacer la base uniquement service arrêté. Conserver la copie précédente et redémarrer une seule réplique. Ne jamais remplacer une base ouverte.

## Exploitation et retour arrière

En cas de 5xx persistants, d’accès anonyme ou de perte de persistance, fermer le trafic et rétablir une release **déjà vérifiée avec authentification**. Ne pas revenir à l’ancienne interface sans login. Préserver le volume : un rollback applicatif ne justifie pas une restauration des données.

Changer le hash de mot de passe ou le secret de session révoque les sessions. Une rotation de clé API doit être coordonnée entre les deux services. Compléter la limitation locale des tentatives de connexion par une règle hébergeur sur `/api/auth/login` ; le compteur local est propre à chaque instance.

Références : [configuration Railway](https://docs.railway.com/config-as-code/reference), [volumes](https://docs.railway.com/volumes), [healthchecks](https://docs.railway.com/deployments/healthchecks), [sauvegardes](https://docs.railway.com/volumes/backups).
