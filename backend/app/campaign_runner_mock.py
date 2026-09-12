"""Compatibility entry point for the deterministic, zero-I/O campaign preview."""

import json
from dataclasses import asdict
from pathlib import Path

from backend.app.campaign_runner import CampaignResult, run_campaign


async def run_campaign_mock(
    seed_list: list[dict],
    campaign_name: str = "campaign-saas-fr-v1",
    output_file: str | None = None,
) -> CampaignResult:
    """Preview supplied leads; only write a report when explicitly requested."""
    result = await run_campaign(seed_list, campaign_name, dry_run=True)
    if output_file:
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return result
