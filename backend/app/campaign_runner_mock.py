"""Mock campaign runner — deterministic pipeline, zero API calls.

Used for smoke testing without LLM API keys.
Each agent returns pre-canned responses based on fixture data.
"""

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.adapters.mocks import (
    MockEmailAdapter,
    MockLinkedInAdapter,
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


# ── Mock agent responses ──────────────────────────────

MOCK_ENRICHMENT = {
    "name": "{company}",
    "size": "50-200",
    "industry": "SaaS / Logiciels",
    "tech_stack": ["AWS", "PostgreSQL", "React", "Python"],
    "signals": ["Hiring engineers", "Product-led growth"],
}

MOCK_ICP_SCORES = [
    {
        "score": 78,
        "breakdown": {"fit": 40, "signals": 25, "timing": 13},
        "verdict": "go",
        "reasoning": "Strong SaaS fit, growing team",
    },
    {
        "score": 72,
        "breakdown": {"fit": 38, "signals": 22, "timing": 12},
        "verdict": "go",
        "reasoning": "Good product fit, recent funding",
    },
    {
        "score": 55,
        "breakdown": {"fit": 30, "signals": 15, "timing": 10},
        "verdict": "nurture",
        "reasoning": "Right sector but too small",
    },
    {
        "score": 82,
        "breakdown": {"fit": 42, "signals": 28, "timing": 12},
        "verdict": "go",
        "reasoning": "Ideal ICP match, buying signals strong",
    },
    {
        "score": 68,
        "breakdown": {"fit": 35, "signals": 20, "timing": 13},
        "verdict": "go",
        "reasoning": "Good fit, active hiring",
    },
]

MOCK_SEQUENCE = {
    "steps": [
        {
            "step": 1,
            "channel": "email",
            "subject": "{first_name}, question sur {company}",
            "body": "Bonjour {first_name},\n\nJe suis tombé sur {company} via LinkedIn — votre croissance est impressionnante.\n\nOn aide les CTOs de SaaS B2B français à générer 15+ meetings qualifiés par mois sans équipe SDR.\n\nEst-ce que l'outbound est un sujet pour vous en ce moment ?\n\n—\nEnzo Buidine | Outreached\n\nDésinscription: https://outreached.io/optout",
            "delay_days": 0,
        },
        {
            "step": 2,
            "channel": "linkedin",
            "subject": "",
            "body": "Hello {first_name}, j'ai vu votre post sur {topic} — très intéressant. On aide justement des équipes comme la vôtre sur ce sujet. Dispo pour échanger ?",
            "delay_days": 3,
        },
    ]
}

MOCK_CLASSIFICATIONS = [
    {
        "intent": "interested",
        "confidence": 0.92,
        "routed_to": "closer",
        "summary": "Hot lead — asking for more info",
    },
    {"intent": "objection", "confidence": 0.85, "routed_to": None, "summary": "Budget objection"},
    {"intent": "not_now", "confidence": 0.78, "routed_to": None, "summary": "Timing not right"},
]

SIMULATED_REPLIES = [
    "Bonjour, je suis intéressé. Pouvez-vous m'envoyer plus d'infos ?",
    "Pas de budget pour le moment, revenez dans 6 mois.",
    "Intéressant mais pas maintenant.",
]


async def run_campaign_mock(
    seed_list: list[dict],
    campaign_name: str = "campaign-saas-fr-v1",
    output_file: str | None = None,
) -> CampaignResult:
    """Run the full pipeline with deterministic mock agents (zero API calls).

    This is the smoke test target: it exercises the full pipeline structure
    without requiring any LLM API keys.
    """
    init_db()
    tenant_id, playbook_id = _ensure_dev_tenant()
    assert playbook_id, "No playbook — run init_db() first"

    # Seed playbook if empty
    store = get_playbook_store()
    existing = store.search_by_type("objection", top_k=1)
    if not existing:
        seed_saas_fr(store, playbook_id)

    email = MockEmailAdapter()
    linkedin = MockLinkedInAdapter()

    result = CampaignResult(campaign_name=campaign_name, leads_processed=len(seed_list))

    for i, lead in enumerate(seed_list):
        company = lead.get("company_name", f"Lead-{i}")
        domain = lead.get("domain", "")
        lead_result = {"company": company, "domain": domain, "steps": []}

        try:
            # Step 1: Enrichment (mock)
            enriched_data = dict(MOCK_ENRICHMENT)
            enriched_data["name"] = company
            lead_result["steps"].append(
                {
                    "step": "enrichment",
                    "model": "mock",
                    "success": True,
                }
            )

            # Step 2: ICP Scoring (mock)
            score_data = MOCK_ICP_SCORES[i % len(MOCK_ICP_SCORES)]
            lead_result["steps"].append(
                {
                    "step": "icp_scoring",
                    "model": "mock",
                    "score": score_data["score"],
                    "verdict": score_data["verdict"],
                }
            )

            if score_data["verdict"] != "go":
                lead_result["skipped"] = f"ICP score too low: {score_data['score']}"
                result.details.append(lead_result)
                continue

            # Step 3: Sequence Writer (mock)
            sequence = dict(MOCK_SEQUENCE)
            first_name = lead.get("first_name", "Contact")
            topic = random.choice(["DevOps", "scaling", "recrutement tech", "cloud"])
            for step in sequence["steps"]:
                step["body"] = step["body"].replace("{first_name}", first_name)
                step["body"] = step["body"].replace("{company}", company)
                step["subject"] = step.get("subject", "").replace("{first_name}", first_name)
                step["subject"] = step.get("subject", "").replace("{company}", company)
                step["body"] = step["body"].replace("{topic}", topic)

            result.sequences_generated += 1
            lead_result["steps"].append(
                {
                    "step": "sequence_writer",
                    "model": "mock",
                    "num_steps": len(sequence["steps"]),
                }
            )

            # Step 4: Send (dry-run)
            for step in sequence["steps"]:
                channel = step["channel"]
                if channel == "email":
                    send_result = await email.send(
                        to_email=lead.get(
                            "email", f"lead@{domain}" if domain else "unknown@test.com"
                        ),
                        subject=step.get("subject", ""),
                        body=step["body"],
                        dry_run=True,
                    )
                elif channel == "linkedin":
                    send_result = await linkedin.send_message(
                        linkedin_url=lead.get("linkedin_url", f"https://linkedin.com/in/test-{i}"),
                        message=step["body"],
                        dry_run=True,
                    )
                else:
                    continue
                lead_result["steps"].append(
                    {
                        "step": f"send_{channel}",
                        "dry_run": True,
                        "external_id": send_result.external_id,
                    }
                )

            # Step 5: Reply Classification (mock)
            reply = SIMULATED_REPLIES[i % len(SIMULATED_REPLIES)]
            classification = MOCK_CLASSIFICATIONS[i % len(MOCK_CLASSIFICATIONS)]
            result.replies_classified += 1
            lead_result["steps"].append(
                {
                    "step": "reply_classifier",
                    "model": "mock",
                    "reply": reply,
                    "intent": classification["intent"],
                    "confidence": classification["confidence"],
                    "routed_to": classification["routed_to"],
                }
            )

            # Simulate cost
            mock_cost = random.uniform(0.001, 0.05)
            result.total_cost_eur += mock_cost

        except Exception as e:
            result.errors.append(f"Pipeline error for {company}: {e}")
            lead_result["error"] = str(e)

        result.details.append(lead_result)

    # Log aggregate agent run
    await log_agent_run(
        tenant_id=tenant_id,
        agent_type="campaign_runner",
        model="mock",
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

    if output_file:
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "campaign": result.campaign_name,
                    "leads_processed": result.leads_processed,
                    "sequences_generated": result.sequences_generated,
                    "replies_classified": result.replies_classified,
                    "total_cost_eur": round(result.total_cost_eur, 4),
                    "errors": result.errors,
                    "details": result.details,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    return result
