"""Shell-level proof: production startup fails closed and the launch gate holds."""

import os
import subprocess
import sys
import tempfile

# Never inherit user data paths or paid-provider credentials into proof runs.
workspace = tempfile.TemporaryDirectory(prefix="outreached-gates-")
BASE_ENV = {
    **os.environ,
    "DEBUG": "false",
    "WORKSPACE_DB_PATH": os.path.join(workspace.name, "workspace.db"),
    "COMMERCIAL_LAUNCH_ENABLED": "false",
    **{
        name: ""
        for name in (
            "DEEPSEEK_API_KEY",
            "KIMI_API_KEY",
            "ANTHROPIC_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "SMARTLEAD_API_KEY",
            "APOLLO_API_KEY",
            "UNIPILE_API_KEY",
        )
    },
}

CASES = [
    (
        "production / no secrets",
        {"APP_ENV": "production", "WORKSPACE_API_KEY": "", "SECRET_KEY": ""},
        "WORKSPACE_API_KEY",
    ),
    (
        "production / missing SECRET_KEY",
        {
            "APP_ENV": "production",
            "WORKSPACE_API_KEY": "k" * 40,
            "SECRET_KEY": "",
            "ALLOWED_HOSTS": '["api.outreached.io"]',
            "ALLOWED_ORIGINS": '["https://app.outreached.io"]',
        },
        "SECRET_KEY",
    ),
    (
        "production / placeholder SECRET_KEY",
        {
            "APP_ENV": "production",
            "WORKSPACE_API_KEY": "k" * 40,
            "SECRET_KEY": "change-me-in-production-at-least-32-chars",
            "ALLOWED_HOSTS": '["api.outreached.io"]',
            "ALLOWED_ORIGINS": '["https://app.outreached.io"]',
        },
        "SECRET_KEY",
    ),
    (
        "production / no ALLOWED_HOSTS",
        {
            "APP_ENV": "production",
            "WORKSPACE_API_KEY": "k" * 40,
            "SECRET_KEY": "s" * 40,
            "ALLOWED_HOSTS": "[]",
            "ALLOWED_ORIGINS": '["https://app.outreached.io"]',
        },
        "ALLOWED_HOSTS",
    ),
    (
        "production / wildcard origin",
        {
            "APP_ENV": "production",
            "WORKSPACE_API_KEY": "k" * 40,
            "SECRET_KEY": "s" * 40,
            "ALLOWED_HOSTS": '["api.outreached.io"]',
            "ALLOWED_ORIGINS": '["*"]',
        },
        "ALLOWED_ORIGINS",
    ),
]

STARTUP = (
    "from backend.app.main import create_app\n"
    "from fastapi.testclient import TestClient\n"
    "try:\n"
    "    with TestClient(create_app(), base_url='http://api.outreached.io') as c:\n"
    "        print('STARTED', c.get('/health').status_code)\n"
    "    with TestClient(create_app()) as c:\n"
    "        print('BADHOST', c.get('/health').status_code)\n"
    "    with TestClient(create_app()) as c:\n"
    "        r = c.post('/api/campaign/run', json={'seed_list': [{'company_name': 'X'}], 'dry_run': False}, headers={'host': 'api.outreached.io', 'authorization': 'Bearer ' + 'k' * 40})\n"
    "        print('LIVE', r.status_code, r.json().get('detail', '')[:60])\n"
    "    with TestClient(create_app(), base_url='http://api.outreached.io') as c:\n"
    "        r = c.post('/api/campaign/run', json={'seed_list': [{'company_name': 'X'}]}, headers={'authorization': 'Bearer ' + 'k' * 40})\n"
    "        print('DRYRUN', r.status_code, r.json().get('mode'))\n"
    "except Exception as exc:\n"
    "    print('FAILED', type(exc).__name__, exc)\n"
)

failures = 0
for label, env, expected in CASES:
    full = {**BASE_ENV, **env}
    out = subprocess.run(
        [sys.executable, "-c", STARTUP], capture_output=True, text=True, env=full
    ).stdout.strip()
    ok = out.startswith("FAILED") and expected in out
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {out[:140]}")

# valid production config must start
valid = {
    **BASE_ENV,
    "APP_ENV": "production",
    "WORKSPACE_API_KEY": "k" * 40,
    "SECRET_KEY": "s" * 40,
    "ALLOWED_HOSTS": '["api.outreached.io"]',
    "ALLOWED_ORIGINS": '["https://app.outreached.io"]',
}
out = subprocess.run(
    [sys.executable, "-c", STARTUP], capture_output=True, text=True, env=valid
).stdout.strip()
checks = {
    "STARTED 200": "starts on the production host",
    "BADHOST 400": "rejects an unlisted host",
    "LIVE 403": "refuses a live run with the launch gate off",
    "DRYRUN 200 demo": "allows dry-run",
}
ok = all(needle in out for needle in checks)
failures += 0 if ok else 1
print(f"[{'PASS' if ok else 'FAIL'}] valid production behaviour:")
for needle, label in checks.items():
    print(f"    {'ok  ' if needle in out else 'MISS'} {label} ({needle})")
print(f"    raw: {out!r}")

print(f"\n{'ALL NEGATIVE PROOFS PASS' if not failures else f'{failures} PROOF(S) FAILED'}")
sys.exit(1 if failures else 0)
