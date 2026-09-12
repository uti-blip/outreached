"""CLI for zero-network campaign previews and configuration checks."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import typer

from backend.app.adapters.factory import LiveSendNotConfiguredError
from backend.app.campaign_runner import run_campaign

app = typer.Typer(name="outreached", help="B2B outbound workspace CLI")


@app.command(name="run-campaign")
def run_campaign_cmd(
    seed_list: str = typer.Option(..., help="Path to JSON seed list file"),
    name: str = typer.Option("campaign-saas-fr-v1", help="Campaign name"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--live", help="Preview only; live is unavailable"
    ),
    output: str | None = typer.Option(None, help="Explicit output JSON path (optional)"),
):
    """Preview supplied leads without provider calls, sending or database writes."""
    try:
        leads = json.loads(Path(seed_list).read_text(encoding="utf-8"))
        if not isinstance(leads, list) or not all(isinstance(lead, dict) for lead in leads):
            raise ValueError("seed list must be a JSON array of objects")
        result = asyncio.run(run_campaign(leads, campaign_name=name, dry_run=dry_run))
    except (OSError, UnicodeError, ValueError, LiveSendNotConfiguredError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Campaign preview: {result.campaign_name}")
    typer.echo(f"Leads inspected: {result.leads_processed}")
    typer.echo(f"Sequences generated: {result.sequences_generated}")
    typer.echo(f"Messages sent: {result.messages_sent}")
    typer.echo(f"Replies classified: {result.replies_classified}")
    typer.echo(f"Provider cost: {result.total_cost_eur:.4f} EUR")
    for error in result.errors:
        typer.echo(f"Error: {error}", err=True)

    if output:
        out_path = Path(output)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            payload = asdict(result)
            payload["campaign"] = payload.pop("campaign_name")
            out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Error writing report: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Preview report written to {out_path}")
    if result.errors:
        raise typer.Exit(1)


@app.command()
def health():
    """Validate configuration without creating databases or contacting services."""
    from backend.app.config import settings

    try:
        settings.validate_production()
    except RuntimeError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Configuration valid. External services and database availability were not checked.")


if __name__ == "__main__":
    app()
