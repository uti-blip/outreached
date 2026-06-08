"""CLI for running outbound campaigns. Phase 1: Typer-based.

Usage:
    uv run python -m backend.app.cli run-campaign --seed-list tests/fixtures/seed_saas_fr.json
    uv run python -m backend.app.cli run-campaign --seed-list tests/fixtures/seed_saas_fr.json --live
"""

import asyncio
import json
from pathlib import Path

import typer

app = typer.Typer(name="outreached", help="B2B Outbound Orchestration CLI")


@app.command(name="run-campaign")
def run_campaign_cmd(
    seed_list: str = typer.Option(..., help="Path to JSON seed list file"),
    name: str = typer.Option("campaign-saas-fr-v1", help="Campaign name"),
    live: bool = typer.Option(False, help="Use real LLM calls (requires API keys)"),
    output: str | None = typer.Option(None, help="Path to output JSON file"),
):
    """Run the full outbound pipeline on a seed list.

    Default: mock mode (deterministic, no API keys needed).
    Use --live for real LLM inference (requires DEEPSEEK_API_KEY, etc.).
    """
    seed_path = Path(seed_list)
    if not seed_path.exists():
        typer.echo(f"Error: seed list not found at {seed_list}", err=True)
        raise typer.Exit(1)

    leads = json.loads(seed_path.read_text())
    if not isinstance(leads, list):
        typer.echo("Error: seed list must be a JSON array", err=True)
        raise typer.Exit(1)

    if live:
        from backend.app.campaign_runner import run_campaign

        typer.echo(f"Running campaign '{name}' on {len(leads)} leads (LIVE LLM, dry-run sends)...")
        result = asyncio.run(run_campaign(seed_list=leads, campaign_name=name, dry_run=True))
    else:
        from backend.app.campaign_runner_mock import run_campaign_mock

        typer.echo(f"Running campaign '{name}' on {len(leads)} leads (mock mode)...")
        result = asyncio.run(
            run_campaign_mock(seed_list=leads, campaign_name=name, output_file=output)
        )

    # ── Summary ───────────────────────────────────
    typer.echo(f"\n{'='*60}")
    typer.echo(f" Campaign: {result.campaign_name}")
    typer.echo(f" Leads processed: {result.leads_processed}")
    typer.echo(f" Sequences generated: {result.sequences_generated}")
    typer.echo(f" Replies classified: {result.replies_classified}")
    typer.echo(f" Total cost (mock): {result.total_cost_eur:.4f} €")
    typer.echo(f" Errors: {len(result.errors)}")
    if result.errors:
        for err in result.errors[:5]:
            typer.echo(f"  - {err}")
    typer.echo(f"{'='*60}")

    # Always write output
    out_path = Path(output) if output else Path("/tmp/campaign_result.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "campaign": result.campaign_name,
                "leads_processed": result.leads_processed,
                "sequences_generated": result.sequences_generated,
                "replies_classified": result.replies_classified,
                "total_cost_eur": result.total_cost_eur,
                "errors": result.errors,
                "details": result.details,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    typer.echo(f"\nFull results written to {out_path}")


@app.command()
def health():
    """Check system health."""
    from backend.app.db.supabase import init_db

    init_db()
    typer.echo("✓ Database initialized")
    typer.echo("✓ Ready")


if __name__ == "__main__":
    app()
