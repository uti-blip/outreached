"""Production launcher safety and actual child-process supervision."""

import json
import os
import secrets
import sys
import threading
from pathlib import Path

import pytest

from scripts.serve_free import INTERNAL_BACKEND, runtime_environment, supervise


def configured_environment() -> dict[str, str]:
    return {
        "WORKSPACE_DATABASE_URL": "postgresql://outreached_app:unused@db.example.test/workspace",
        "WORKSPACE_PUBLIC_ORIGIN": "https://workspace.example.test",
        "WORKSPACE_API_KEY": secrets.token_urlsafe(32),
        "SECRET_KEY": secrets.token_urlsafe(48),
        "WORKSPACE_SESSION_SECRET": secrets.token_urlsafe(48),
        "WORKSPACE_LOGIN_USER": "owner",
        "WORKSPACE_PASSWORD_HASH": "configured-by-auth-setup",
        "BACKEND_INTERNAL": "true",
        "BACKEND_URL": INTERNAL_BACKEND,
    }


def test_runtime_requires_external_database_and_exact_private_api():
    env = configured_environment()
    for changes in (
        {"WORKSPACE_DATABASE_URL": ""},
        {"WORKSPACE_DATABASE_URL": "sqlite:///tmp/workspace.db"},
        {"BACKEND_INTERNAL": "false"},
        {"BACKEND_URL": "http://localhost:8001"},
        {"BACKEND_URL": "https://another-api.example.test"},
        {"WORKSPACE_PUBLIC_ORIGIN": "http://workspace.example.test"},
        {"WORKSPACE_PUBLIC_ORIGIN": "https://workspace.example.test/login"},
        {"WORKSPACE_PUBLIC_ORIGIN": "https://user:secret@workspace.example.test"},
        {"WORKSPACE_API_KEY": "too-short"},
        {"SECRET_KEY": ""},
        {"SECRET_KEY": env["WORKSPACE_API_KEY"]},
        {"WORKSPACE_SESSION_SECRET": ""},
        {"PORT": "8001"},
        {"PORT": "80"},
        {"PORT": "invalid"},
    ):
        with pytest.raises(ValueError):
            runtime_environment(env | changes)


def test_runtime_restricts_backend_hosts_to_the_public_workspace():
    env = runtime_environment(
        configured_environment()
        | {"PORT": "11000", "ALLOWED_HOSTS": '["*"]', "APP_ENV": "development"}
    )
    assert env["APP_ENV"] == env["NODE_ENV"] == "production"
    assert env["HOSTNAME"] == "0.0.0.0"
    assert env["PORT"] == "11000"
    assert json.loads(env["ALLOWED_HOSTS"]) == ["workspace.example.test"]
    assert json.loads(env["ALLOWED_ORIGINS"]) == ["https://workspace.example.test"]


def child_command(code: str, directory: Path):
    return ("test-child", [sys.executable, "-c", code], directory)


def test_child_failure_terminates_its_live_sibling(tmp_path):
    # The failed child waits until its sibling installed a SIGTERM handler.
    # The marker proves the running sibling receives graceful termination.
    sleeping = """
import signal, time
from pathlib import Path
def stop(*_):
    Path('terminated').write_text('yes')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
Path('ready').touch()
while True:
    time.sleep(0.05)
"""
    failing = """
import time
from pathlib import Path
deadline = time.monotonic() + 5
while not Path('ready').exists() and time.monotonic() < deadline:
    time.sleep(0.01)
raise SystemExit(7)
"""
    assert (
        supervise(
            [child_command(sleeping, tmp_path), child_command(failing, tmp_path)], dict(os.environ)
        )
        == 1
    )
    assert (tmp_path / "terminated").read_text() == "yes"


def test_shutdown_request_stops_and_reaps_children(tmp_path):
    stop = threading.Event()
    timer = threading.Timer(0.5, stop.set)
    timer.start()
    try:
        assert (
            supervise(
                [child_command("import time; time.sleep(30)", tmp_path)], dict(os.environ), stop
            )
            == 0
        )
    finally:
        timer.cancel()


def test_partial_spawn_failure_cleans_up_started_child(tmp_path):
    # An invalid second executable must not leave the first process alive.
    import scripts.serve_free as launcher

    real_spawn = launcher.subprocess.Popen
    started = []

    def spawn(*args, **kwargs):
        child = real_spawn(*args, **kwargs)
        started.append(child)
        return child

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(launcher.subprocess, "Popen", spawn)
        with pytest.raises(FileNotFoundError):
            supervise(
                [
                    child_command("import time; time.sleep(30)", tmp_path),
                    ("missing", [str(tmp_path / "missing-executable")], tmp_path),
                ],
                dict(os.environ),
            )
    assert len(started) == 1
    assert started[0].poll() is not None
