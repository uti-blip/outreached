"""Run the private API and public frontend together on ephemeral hosting.

Persistence is external PostgreSQL. Neither process can silently keep serving
when its sibling fails. No business operation is used for health checking.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

INTERNAL_BACKEND = "http://127.0.0.1:8001"


def runtime_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Validate the combined deployment before spawning either process."""
    env = dict(source)
    for key in (
        "WORKSPACE_DATABASE_URL",
        "WORKSPACE_LOGIN_USER",
        "WORKSPACE_PASSWORD_HASH",
        "WORKSPACE_SESSION_SECRET",
        "WORKSPACE_API_KEY",
        "SECRET_KEY",
        "WORKSPACE_PUBLIC_ORIGIN",
    ):
        if not env.get(key):
            raise ValueError(f"{key} is required for the combined deployment")
    if urlsplit(env["WORKSPACE_DATABASE_URL"]).scheme not in {"postgres", "postgresql"}:
        raise ValueError("WORKSPACE_DATABASE_URL must use PostgreSQL, not ephemeral SQLite")
    origin = urlsplit(env["WORKSPACE_PUBLIC_ORIGIN"])
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
    ):
        raise ValueError("WORKSPACE_PUBLIC_ORIGIN must be an HTTPS origin without a path")
    # Match the exact canonical origin used by the Next.js auth validator.
    env["WORKSPACE_PUBLIC_ORIGIN"] = f"https://{origin.netloc}"
    if env.get("BACKEND_INTERNAL") != "true" or env.get("BACKEND_URL") != INTERNAL_BACKEND:
        raise ValueError(
            "Combined deployment requires BACKEND_INTERNAL=true and its fixed loopback URL"
        )
    try:
        port = int(env.get("PORT", "10000"))
    except ValueError as error:
        raise ValueError("PORT must be a valid unprivileged frontend port") from error
    if not 1024 <= port <= 65535 or port == 8001:
        raise ValueError("PORT must be an unprivileged port other than the private backend port")
    secrets = [env[key] for key in ("WORKSPACE_API_KEY", "WORKSPACE_SESSION_SECRET", "SECRET_KEY")]
    if any(len(value) < 32 for value in secrets) or len(set(secrets)) != len(secrets):
        raise ValueError(
            "Workspace, session and backend secrets must be distinct and at least 32 characters"
        )
    env.update(
        APP_ENV="production",
        NODE_ENV="production",
        PORT=str(port),
        HOSTNAME="0.0.0.0",
        ALLOWED_HOSTS=json.dumps([origin.hostname]),
        ALLOWED_ORIGINS=json.dumps([env["WORKSPACE_PUBLIC_ORIGIN"]]),
    )
    return env


def stop_children(children: Sequence[subprocess.Popen], timeout: float = 10.0) -> None:
    """Forward termination to child process groups, then reap every child."""
    for child in children:
        if child.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def supervise(
    commands: Sequence[tuple[str, Sequence[str], Path]],
    env: Mapping[str, str],
    stop: threading.Event | None = None,
) -> int:
    """Exit nonzero and stop the sibling if any child exits unexpectedly."""
    stopping = stop if stop is not None else threading.Event()
    previous_handlers = {}
    children: list[subprocess.Popen] = []
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, lambda *_: stopping.set())
        for _name, command, directory in commands:
            children.append(
                subprocess.Popen(command, cwd=directory, env=env, start_new_session=True)
            )
        while not stopping.wait(0.2):
            for (name, _, _), child in zip(commands, children, strict=True):
                code = child.poll()
                if code is not None:
                    print(f"{name} exited with status {code}; stopping the service", flush=True)
                    return 1
        return 0
    finally:
        stop_children(children)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    if os.geteuid() == 0:
        raise RuntimeError("The combined container must run as its unprivileged image user")
    os.umask(0o077)
    env = runtime_environment(os.environ)
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    if not (frontend / "server.js").is_file():
        raise RuntimeError("Build the standalone frontend before starting the combined container")
    return supervise(
        [
            (
                "backend",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8001",
                    "--workers",
                    "1",
                    "--no-proxy-headers",
                    "--limit-concurrency",
                    "40",
                    "--timeout-keep-alive",
                    "5",
                ],
                root,
            ),
            ("frontend", ["node", "server.js"], frontend),
        ],
        env,
    )


if __name__ == "__main__":
    sys.exit(main())
