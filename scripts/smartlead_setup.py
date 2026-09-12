#!/usr/bin/env python3
"""Plan mailbox setup locally. Smartlead provisioning and warmup are unavailable.

No API key is needed for a plan. This command never creates a mailbox, initiates
warmup email, or claims that provider setup completed.
"""

import re

import typer

app = typer.Typer(help="Local Smartlead mailbox setup plan")


@app.command()
def provision(
    domain: str = typer.Option(..., help="Secondary domain already owned by the operator"),
    count: int = typer.Option(3, min=1, max=20, help="Number of planned mailboxes"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Plan only; live is unavailable"),
    api_key: str = typer.Option("", envvar="SMARTLEAD_API_KEY", help="Unused compatibility option"),
):
    """Print proposed mailbox addresses; no provisioning or warmup is performed."""
    if not dry_run:
        typer.echo(
            "Provisioning and warmup are unavailable: provider integration is unverified.", err=True
        )
        raise typer.Exit(1)
    if len(domain) > 253 or not re.fullmatch(
        r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", domain
    ):
        raise typer.BadParameter("Supply a domain name without a URL, path or email address.")
    typer.echo("Local plan only — no mailboxes created and no warmup started.")
    for index in range(count):
        typer.echo(f"Planned mailbox: outreach{index + 1}@{domain.lower()}")
    typer.echo("Complete mailbox setup in the provider dashboard and verify DNS and ownership.")


@app.command()
def status(
    api_key: str = typer.Option("", envvar="SMARTLEAD_API_KEY", help="Unused compatibility option"),
):
    """Report the missing provider capability without claiming a delivery state."""
    typer.echo("Mailbox status is unavailable: provider integration is unverified.", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
