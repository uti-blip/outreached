# Lexia frontend

Private, single-workspace prospecting interface. Messages are prepared here and sent manually from the owner's email client. The frontend does not send campaigns automatically.

Use Node 22 (22.14 or newer) and pnpm 10.33.0. From the repository root, `./scripts/dev.sh` starts the local application. See the repository deployment guide for persistent backend storage and hosting.

## Authentication setup

From the repository root:

```sh
node frontend/scripts/setup-auth.mjs --output .runtime/auth --user admin --origin http://localhost:3000 --backend-url http://127.0.0.1:8001
```

The helper writes `frontend.env` and `workspace-login.txt` with owner-only file permissions and refuses to overwrite existing files. It prints file locations, never secrets. Keep the credentials in a password manager and keep generated files outside version control. The generated `WORKSPACE_API_KEY` must match the backend key.

For the combined container, use your HTTPS frontend origin and add `--internal`. This explicitly enables only the fixed loopback backend. Remote production backends still require HTTPS.

Configure the server environment (never `NEXT_PUBLIC_*`):

| Variable | Purpose |
| --- | --- |
| `WORKSPACE_LOGIN_USER` | Workspace owner's login name |
| `WORKSPACE_PASSWORD_HASH` | `scrypt:16384:8:1:<32 hex salt>:<128 hex hash>` |
| `WORKSPACE_SESSION_SECRET` | Random signing secret, at least 32 characters |
| `WORKSPACE_PUBLIC_ORIGIN` | Exact frontend origin; HTTPS required in production |
| `WORKSPACE_API_KEY` | Shared backend credential |
| `BACKEND_URL` | Backend origin; HTTPS required for a remote production backend |
| `BACKEND_INTERNAL` | Set to `true` only for the combined container with the exact backend URL `http://127.0.0.1:8001` |
| `SECRET_KEY` | Separate backend application secret generated for the combined container |

The browser receives an 8-hour signed HttpOnly, SameSite=Strict session cookie, with Secure and the `__Host-` prefix in production. Every workspace API request verifies it. Writes also require the configured Origin and a session-bound CSRF token. Credential rotation revokes all existing sessions; logging out clears the current browser cookie. A copied cookie remains valid until expiry or credential rotation.

Login attempts have bounded per-instance throttling. Configure rate limiting for `/api/auth/login` at the deployment ingress as well, because serverless instances do not share counters. This application has one workspace owner account, no self-service registration or password recovery.

Only the manual workspace API is proxied. The automated campaign runner is not exposed through this frontend. Backend credentials, network details, and raw upstream server failures are not returned to the browser.

## Verification

```sh
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm typecheck
pnpm build
pnpm audit --prod
```

`GET /health` returns 200 only when authentication is configured and the backend database readiness check succeeds; otherwise it returns a generic 503. The combined Render container runs the API on loopback and the Next.js standalone server on the public port. PostgreSQL must be hosted externally because the free web service filesystem is ephemeral. The original volume-backed deployment remains available separately.
