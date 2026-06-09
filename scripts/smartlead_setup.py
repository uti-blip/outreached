#!/usr/bin/env python3
"""Smartlead warmup & mailbox provisioning.

Usage:
    uv run python scripts/smartlead_setup.py provision --api-key sl_...
    uv run python scripts/smartlead_setup.py status --api-key sl_...

Phase 1: provisions 3 mailboxes on a secondary domain, starts warmup.
Warmup takes 2-3 weeks before first real send.
"""

import asyncio

import httpx
import typer

app = typer.Typer(help="Smartlead mailbox provisioning & warmup")

SMARTLEAD_API = "https://server.smartlead.ai/api/v1"


async def _client(api_key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=SMARTLEAD_API,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )


@app.command()
def provision(
    api_key: str = typer.Option(..., help="Smartlead API key"),
    domain: str = typer.Option(
        ..., help="Secondary domain for cold email (e.g., outreached-mail.com)"
    ),
    count: int = typer.Option(3, help="Number of mailboxes to create"),
):
    """Provision mailboxes and start warmup."""

    async def _provision():
        async with await _client(api_key) as client:
            typer.echo(f"Provisioning {count} mailboxes on {domain}...")

            mailboxes = []
            for i in range(count):
                email = f"outreach{i+1}@{domain}"
                # Smartlead API: create email account
                resp = await client.post(
                    "/email-accounts/create",
                    json={
                        "email": email,
                        "from_name": "Enzo Buidine",
                        "signature": "Enzo Buidine — Outreached",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    typer.echo(f"  ✓ {email} — ID: {data.get('id', 'unknown')}")
                    mailboxes.append(data)
                else:
                    typer.echo(f"  ✗ {email} — {resp.status_code}: {resp.text[:200]}")

            if not mailboxes:
                typer.echo("No mailboxes created. Aborting warmup.", err=True)
                raise typer.Exit(1)

            # Start warmup for all mailboxes
            typer.echo(f"\nStarting warmup for {len(mailboxes)} mailboxes...")
            for mb in mailboxes:
                resp = await client.post(
                    "/email-accounts/warmup",
                    json={"email_account_id": mb["id"]},
                )
                if resp.status_code == 200:
                    typer.echo(f"  ✓ Warmup started for {mb['email']}")
                else:
                    typer.echo(f"  ✗ Warmup failed for {mb['email']}: {resp.text[:200]}")

            typer.echo(
                f"\n✓ Done. Warmup takes 2-3 weeks. Monitor with: status --api-key {api_key[:8]}..."
            )
            typer.echo("  ⚠️  DO NOT send cold emails before warmup completes.")

    asyncio.run(_provision())


@app.command()
def status(api_key: str = typer.Option(..., help="Smartlead API key")):
    """Check warmup status of all mailboxes."""

    async def _status():
        async with await _client(api_key) as client:
            resp = await client.get("/email-accounts/list")
            if resp.status_code != 200:
                typer.echo(f"Error: {resp.status_code} — {resp.text[:200]}", err=True)
                raise typer.Exit(1)

            accounts = resp.json()
            if not accounts:
                typer.echo("No email accounts found.")
                return

            typer.echo(f"{'Email':<30} {'Warmup':<12} {'Status':<12} {'Daily Limit'}")
            typer.echo("-" * 70)
            for acct in accounts:
                email = acct.get("email", "unknown")
                warmup = acct.get("warmup_status", "unknown")
                status_ = acct.get("status", "unknown")
                limit = acct.get("daily_limit", "N/A")
                icon = "✓" if warmup == "completed" else "⏳" if warmup == "in_progress" else "✗"
                typer.echo(f"{icon} {email:<28} {warmup:<12} {status_:<12} {limit}")

    asyncio.run(_status())


if __name__ == "__main__":
    app()
