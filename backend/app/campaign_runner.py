"""Campaign runner — orchestrates the full outbound pipeline.

Pipeline: Sourcing → Enrichment → ICP Scoring → Sequence Writer (RAG) →
          Send DRY-RUN → Reply Classifier (simulated).

Phase 1: synchronous sequential pipeline (Celery fan-out in Phase 2).
"""

from dataclasses import dataclass, field

from backend.app.adapters.mocks import (
    MockEmailAdapter,
    MockLinkedInAdapter,
)
from backend.app.agents.base import (
    EnrichmentAgent,
    ICPScoringAgent,
    ReplyClassifierAgent,
    SequenceWriterAgent,
)
from backend.app.cost_tracker import log_agent_run
from backend.app.db.supabase import _ensure_dev_tenant, init_db
from backend.app.rag.playbook_store import get_playbook_store
from backend.app.rag.seed_saas_fr import seed_saas_fr


@dataclass
class CampaignResult:
    campaign_name: str
    leads_processed: int
    sequences_generated: int = 0
    replies_classified: int = 0
    total_cost_eur: float = 0.0
    total_latency_ms: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)


async def run_campaign(
    seed_list: list[dict],
    campaign_name: str = "campaign-saas-fr-v1",
    dry_run: bool = True,
) -> CampaignResult:
    """Run the full outbound pipeline on a seed list.

    Args:
        seed_list: List of leads, each with at least {"company_name": "...", "domain": "..."}
        campaign_name: Human-readable name
        dry_run: If True, no real emails/LinkedIn messages sent
    """
    init_db()
    tenant_id, playbook_id = _ensure_dev_tenant()
    if not playbook_id:
        raise RuntimeError("No playbook found — run init_db() first")

    # ── Setup ─────────────────────────────────────
    store = get_playbook_store()

    # Seed playbook if empty
    existing = store.search_by_type("objection", top_k=1)
    if not existing:
        seed_saas_fr(store, playbook_id)

    # Agents
    enricher = EnrichmentAgent()
    scorer = ICPScoringAgent()
    writer = SequenceWriterAgent()
    classifier = ReplyClassifierAgent()

    # Adapters (mock in Phase 1)
    email = MockEmailAdapter()
    linkedin = MockLinkedInAdapter()

    result = CampaignResult(campaign_name=campaign_name, leads_processed=len(seed_list))

    for i, lead in enumerate(seed_list):
        company = lead.get("company_name", f"Lead-{i}")
        domain = lead.get("domain", "")
        lead_result = {"company": company, "domain": domain, "steps": []}

        try:
            # Step 1: Enrichment (DeepSeek v4-flash)
            enriched = await enricher.run({"company_name": company, "domain": domain})
            result.total_cost_eur += enriched.cost_eur
            result.total_latency_ms += enriched.latency_ms
            lead_result["steps"].append(
                {
                    "step": "enrichment",
                    "model": enriched.model,
                    "success": enriched.success,
                }
            )
            if not enriched.success:
                lead_result["steps"][-1]["error"] = enriched.error
                result.errors.append(f"Enrichment failed for {company}: {enriched.error}")
                result.details.append(lead_result)
                continue

            enriched_data = enriched.output

            # Step 2: ICP Scoring (Kimi K2.6)
            playbook_context = store.get_context(f"{company} {enriched_data.get('industry', '')}")
            scored = await scorer.run(
                {
                    "enriched": enriched_data,
                    "playbook_context": playbook_context,
                }
            )
            result.total_cost_eur += scored.cost_eur
            result.total_latency_ms += scored.latency_ms
            lead_result["steps"].append(
                {
                    "step": "icp_scoring",
                    "model": scored.model,
                    "score": scored.output.get("score", 0),
                    "verdict": scored.output.get("verdict", "unknown"),
                }
            )

            if scored.output.get("verdict") != "go":
                lead_result["skipped"] = f"ICP score too low: {scored.output.get('score', 0)}"
                result.details.append(lead_result)
                continue

            # Step 3: Sequence Writer (Kimi K2.6 + RAG)
            sequence = await writer.run(
                {
                    "lead": enriched_data,
                    "playbook_context": playbook_context,
                }
            )
            result.total_cost_eur += sequence.cost_eur
            result.total_latency_ms += sequence.latency_ms
            result.sequences_generated += 1

            steps = sequence.output.get("steps", [])
            lead_result["steps"].append(
                {
                    "step": "sequence_writer",
                    "model": sequence.model,
                    "num_steps": len(steps),
                }
            )

            # Step 4: Send (DRY-RUN by default)
            for step_data in steps:
                channel = step_data.get("channel", "email")
                if channel == "email":
                    send_result = await email.send(
                        to_email=lead.get(
                            "email", f"lead@{domain}" if domain else "unknown@test.com"
                        ),
                        subject=step_data.get("subject", ""),
                        body=step_data.get("body", ""),
                        dry_run=dry_run,
                    )
                elif channel == "linkedin":
                    send_result = await linkedin.send_message(
                        linkedin_url=lead.get("linkedin_url", ""),
                        message=step_data.get("body", ""),
                        dry_run=dry_run,
                    )
                else:
                    continue

                lead_result["steps"].append(
                    {
                        "step": f"send_{channel}",
                        "dry_run": dry_run,
                        "external_id": send_result.external_id,
                    }
                )

            # Step 5: Simulate a reply and classify it
            simulated_reply = "Bonjour, je suis intéressé. Pouvez-vous m'envoyer plus d'infos ?"
            classified = await classifier.run({"reply_body": simulated_reply})
            result.total_cost_eur += classified.cost_eur
            result.total_latency_ms += classified.latency_ms
            result.replies_classified += 1
            lead_result["steps"].append(
                {
                    "step": "reply_classifier",
                    "intent": classified.output.get("intent"),
                    "routed_to": classified.output.get("routed_to"),
                }
            )

        except Exception as e:
            result.errors.append(f"Pipeline error for {company}: {e}")
            lead_result["error"] = str(e)

        result.details.append(lead_result)

    # ── Log agent runs ────────────────────────────
    await log_agent_run(
        tenant_id=tenant_id,
        agent_type="campaign_runner",
        model="orchestrator",
        input_tokens=0,
        output_tokens=0,
        cost_eur=result.total_cost_eur,
        latency_ms=result.total_latency_ms,
        metadata={
            "campaign": campaign_name,
            "leads": len(seed_list),
            "sequences": result.sequences_generated,
            "replies": result.replies_classified,
            "errors": len(result.errors),
        },
    )

    return result
