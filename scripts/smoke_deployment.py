"""Verify a deployed workspace over HTTP without changing business data.

Login credentials are read only from WORKSPACE_LOGIN_USER and
WORKSPACE_LOGIN_PASSWORD. WORKSPACE_API_KEY optionally verifies the protected
backend read directly. No credentials, cookies, tokens, or workspace data are
printed. The only POSTs are login, an invalid campaign request without CSRF
(which must be rejected), and logout.
"""

import argparse
import os
import re
import sys
from contextlib import suppress
from ipaddress import ip_address
from urllib.parse import urljoin, urlsplit

import httpx


class SmokeError(Exception):
    """Safe, deliberately credential-free diagnostic."""


def validate_origin(value: str, label: str, *, allow_local: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in value)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
        loopback = parsed.hostname == "localhost"
        if parsed.hostname and not loopback:
            with suppress(ValueError):
                loopback = ip_address(parsed.hostname).is_loopback
        allowed_scheme = parsed.scheme == "https" or (
            allow_local and parsed.scheme == "http" and loopback
        )
        if not valid or not allowed_scheme:
            raise ValueError
        return str(httpx.URL(value).copy_with(path="/", query=None, fragment=None)).rstrip("/")
    except (ValueError, httpx.InvalidURL):
        raise SmokeError(
            f"{label}: origine HTTPS requise, sans identifiants, chemin ni paramètres. "
            "--allow-local autorise HTTP uniquement sur loopback."
        ) from None


def request(client: httpx.Client, method: str, path: str, label: str, **kwargs) -> httpx.Response:
    try:
        return client.request(method, path, **kwargs)
    except (httpx.HTTPError, ValueError):
        # Server bodies and exception details can include credentials or data.
        raise SmokeError(f"{label}: requête impossible (réseau ou TLS).") from None


def expect_status(response: httpx.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise SmokeError(f"{label}: HTTP {response.status_code}, attendu {expected}.")


def json_object(response: httpx.Response, label: str) -> dict:
    try:
        value = response.json()
    except ValueError:
        raise SmokeError(f"{label}: réponse JSON invalide.") from None
    if not isinstance(value, dict):
        raise SmokeError(f"{label}: objet JSON attendu.")
    return value


def expect_workspace(response: httpx.Response, label: str) -> None:
    expect_status(response, 200, label)
    value = json_object(response, label)
    if not (
        isinstance(value.get("profile"), dict)
        and isinstance(value.get("leads"), list)
        and isinstance(value.get("campaigns"), list)
    ):
        raise SmokeError(f"{label}: réponse d’espace invalide.")


def verify_deployment(
    frontend_url: str,
    backend_url: str,
    *,
    username: str | None,
    password: str | None,
    workspace_api_key: str | None = None,
    allow_local: bool = False,
    client_factory=httpx.Client,
) -> list[str]:
    """Run the complete checks or fail; never silently omit authentication."""
    frontend = validate_origin(frontend_url, "Frontend", allow_local=allow_local)
    backend = validate_origin(backend_url, "Backend", allow_local=allow_local)
    if not username or not username.strip() or not password or not password.strip():
        raise SmokeError(
            "WORKSPACE_LOGIN_USER et WORKSPACE_LOGIN_PASSWORD sont requis pour la vérification complète."
        )
    if len(username) > 200 or len(password) > 1024:
        raise SmokeError("Les identifiants dépassent les limites du formulaire de connexion.")

    passed = []
    options = {
        "timeout": httpx.Timeout(20, connect=5),
        "follow_redirects": False,
        "trust_env": False,
    }
    # Separate cookie jars prevent frontend credentials reaching the backend,
    # even when local test deployments share a hostname on different ports.
    with (
        client_factory(base_url=backend, **options) as back,
        client_factory(base_url=frontend, **options) as front,
    ):
        for path, status in (("/health", "ok"), ("/health/ready", "ready")):
            label = f"Backend {path}"
            response = request(back, "GET", path, label)
            expect_status(response, 200, label)
            if json_object(response, label).get("status") != status:
                raise SmokeError(f"{label}: état de santé incorrect.")
        passed.append("backend: liveness et stockage disponibles")

        label = "Backend anonyme"
        expect_status(request(back, "GET", "/api/workspace", label), 401, label)
        passed.append("backend: accès anonyme refusé")
        if workspace_api_key:
            label = "Backend authentifié"
            expect_workspace(
                request(
                    back,
                    "GET",
                    "/api/workspace",
                    label,
                    headers={"Authorization": f"Bearer {workspace_api_key}"},
                ),
                label,
            )
            passed.append("backend: lecture avec clé vérifiée")

        label = "Frontend /health"
        response = request(front, "GET", "/health", label)
        expect_status(response, 200, label)
        if json_object(response, label).get("status") != "ok":
            raise SmokeError(f"{label}: état de santé incorrect.")

        label = "Page privée anonyme"
        response = request(front, "GET", "/campaign", label)
        destination = urlsplit(
            urljoin(frontend + "/campaign", response.headers.get("location", ""))
        )
        if response.status_code not in {302, 303, 307, 308} or (
            f"{destination.scheme}://{destination.netloc}" != frontend
            or destination.path != "/login"
        ):
            raise SmokeError(f"{label}: redirection vers la connexion attendue.")
        label = "Frontend anonyme"
        expect_status(request(front, "GET", "/api/workspace", label), 401, label)
        passed.append("frontend: page et API privées protégées")

        label = "Connexion"
        response = request(
            front,
            "POST",
            "/api/auth/login",
            label,
            headers={"Origin": frontend},
            json={"username": username, "password": password},
        )
        expect_status(response, 200, label)
        expected_cookie = (
            "__Host-workspace-session" if frontend.startswith("https://") else "workspace-session"
        )
        session_cookies = [cookie for cookie in front.cookies.jar if cookie.name == expected_cookie]
        if len(session_cookies) != 1:
            raise SmokeError("Connexion: cookie de session attendu absent.")
        cookie = session_cookies[0]
        attributes = {key.lower(): value for key, value in cookie._rest.items()}
        if (
            cookie.path != "/"
            or cookie.domain_specified
            or "httponly" not in attributes
            or str(attributes.get("samesite", "")).lower() != "strict"
            or (frontend.startswith("https://") and not cookie.secure)
        ):
            raise SmokeError("Connexion: attributs du cookie de session non sécurisés.")

        label = "Session"
        response = request(front, "GET", "/api/auth/session", label)
        expect_status(response, 200, label)
        csrf = json_object(response, label).get("csrfToken")
        if not isinstance(csrf, str) or not re.fullmatch(r"[\w-]{43}", csrf):
            raise SmokeError("Session: jeton CSRF attendu absent ou invalide.")
        label = "Lecture frontend authentifiée"
        expect_workspace(request(front, "GET", "/api/workspace", label), label)
        passed.append("frontend: connexion et lecture authentifiée vérifiées")

        # Empty input is invalid business data even if a broken CSRF gate lets it
        # through. No name, lead IDs, draft ID, or send action is ever supplied.
        label = "Protection CSRF"
        response = request(
            front,
            "POST",
            "/api/campaigns",
            label,
            headers={"Origin": frontend},
            json={},
        )
        expect_status(response, 403, label)
        passed.append("frontend: requête sans CSRF refusée")

        label = "Déconnexion"
        response = request(
            front,
            "POST",
            "/api/auth/logout",
            label,
            headers={"Origin": frontend, "X-CSRF-Token": csrf},
            json={},
        )
        expect_status(response, 200, label)
        if any(cookie.name == expected_cookie for cookie in front.cookies.jar):
            raise SmokeError("Déconnexion: cookie de session encore présent dans le client.")
        label = "Accès après déconnexion"
        expect_status(request(front, "GET", "/api/workspace", label), 401, label)
        passed.append("frontend: cookie retiré et client déconnecté")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-url", required=True)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument(
        "--allow-local", action="store_true", help="Allow HTTP only for loopback test servers"
    )
    args = parser.parse_args(argv)
    try:
        passed = verify_deployment(
            args.frontend_url,
            args.backend_url,
            username=os.environ.get("WORKSPACE_LOGIN_USER"),
            password=os.environ.get("WORKSPACE_LOGIN_PASSWORD"),
            workspace_api_key=os.environ.get("WORKSPACE_API_KEY"),
            allow_local=args.allow_local,
        )
    except SmokeError as exc:
        print(f"ÉCHEC — {exc}", file=sys.stderr)
        return 1
    for check in passed:
        print(f"OK — {check}")
    print("Vérification complète réussie ; aucune donnée métier modifiée.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
