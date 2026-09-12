"""Keep the mutating CI probe confined to disposable local targets."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import verify_free_container
from scripts.verify_free_container import VerificationError, WorkspaceClient, snapshot_digest


@pytest.mark.parametrize(
    "address",
    [
        "https://workspace.example.com",
        "http://workspace.example.com",
        "http://user:password@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1?target=remote",
    ],
)
def test_synthetic_probe_rejects_nonlocal_or_ambiguous_targets(address):
    with pytest.raises(VerificationError, match="loopback"):
        WorkspaceClient(address)


def test_snapshot_comparison_ignores_row_order_but_detects_data_loss():
    first = {"leads": [{"id": 1, "status": "do_not_contact"}, {"id": 2, "status": "new"}]}
    reordered = {"leads": list(reversed(first["leads"]))}
    missing = {"leads": first["leads"][:1]}
    assert snapshot_digest(first) == snapshot_digest(reordered)
    assert snapshot_digest(first) != snapshot_digest(missing)


def test_readiness_failure_retains_sanitized_connection_diagnostic(monkeypatch):
    client = WorkspaceClient("http://127.0.0.1:41001")

    def unavailable(*args):
        raise VerificationError("GET /health: connection failed")

    monkeypatch.setattr(client, "request", unavailable)
    monkeypatch.setattr(verify_free_container.time, "sleep", lambda _: None)
    with pytest.raises(VerificationError, match="last probe: GET /health: connection failed"):
        client.ready()


def test_shell_probe_refreshes_ephemeral_port_after_container_restart(tmp_path):
    """Run the real shell lifecycle with a Docker CLI that reallocates its port.

    The business probe itself runs in the real container CI job; this regression
    isolates Docker's changing network mapping without needing a local daemon.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "root = pathlib.Path(os.environ['FAKE_DOCKER_ROOT'])\n"
        "state = root / 'restarted'\n"
        "if sys.argv[1] == 'inspect': print('healthy')\n"
        "elif sys.argv[1] == 'port':\n"
        "    print('127.0.0.1:41002' if state.exists() else '127.0.0.1:41001')\n"
        "elif sys.argv[1] == 'restart' and sys.argv[2].endswith('-app'):\n"
        "    state.touch()\n"
    )
    python = bin_dir / "python3"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "if sys.argv[1] != 'scripts/verify_free_container.py':\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
        "root = pathlib.Path(os.environ['FAKE_DOCKER_ROOT'])\n"
        "address = sys.argv[sys.argv.index('--address') + 1]\n"
        "expected = 'http://127.0.0.1:' + ('41002' if (root / 'restarted').exists() else '41001')\n"
        "assert address == expected, 'Probe used a stale Docker host port after restart'\n"
        "with (root / 'probes').open('a') as output:\n"
        "    output.write(json.dumps({'address': address}) + '\\n')\n"
    )
    docker.chmod(0o755)
    python.chmod(0o755)
    result = subprocess.run(
        ["bash", "scripts/check_free_container.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_DOCKER_ROOT": str(tmp_path),
            "GITHUB_ACTIONS": "false",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert [
        json.loads(line)["address"] for line in (tmp_path / "probes").read_text().splitlines()
    ] == [
        "http://127.0.0.1:41001",
        "http://127.0.0.1:41002",
    ]
