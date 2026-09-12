"""Side-effect-free campaign preview for the agency workspace.

Live orchestration is unavailable until verified providers, message persistence,
idempotence, suppression handling and delivery/reply ingestion are implemented.
Previewing must never spend credits or manufacture commercial evidence.
"""

from dataclasses import dataclass, field

from backend.app.adapters.factory import LIVE_SEND_UNAVAILABLE, LiveSendNotConfiguredError


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
    mode: str = "demo"
    messages_sent: int = 0


async def run_campaign(
    seed_list: list[dict],
    campaign_name: str = "campaign-saas-fr-v1",
    dry_run: bool = True,
) -> CampaignResult:
    """Validate supplied leads and preview pending work, without any I/O.

    Neither installed credentials nor a launch flag may convert this preview
    into paid inference or sending. Replies require genuine inbound records.
    """
    if not dry_run:
        raise LiveSendNotConfiguredError(LIVE_SEND_UNAVAILABLE)
    if not isinstance(seed_list, list) or not all(isinstance(lead, dict) for lead in seed_list):
        raise ValueError("La liste de prospects doit être un tableau d’objets JSON.")

    result = CampaignResult(campaign_name=campaign_name, leads_processed=len(seed_list))
    for index, lead in enumerate(seed_list):
        company = lead.get("company_name", "")
        if not isinstance(company, str) or not company.strip():
            error = f"Prospect {index + 1} : company_name est requis."
            result.errors.append(error)
            result.details.append({"input_index": index, "status": "invalid", "error": error})
            continue

        channels = []
        if isinstance(lead.get("email"), str) and lead["email"].strip():
            channels.append("email")
        if isinstance(lead.get("linkedin_url"), str) and lead["linkedin_url"].strip():
            channels.append("linkedin")
        result.details.append(
            {
                "company": company.strip(),
                "domain": lead.get("domain", ""),
                "status": "preview",
                "simulated": True,
                "supplied_channels": channels,
                "steps": [
                    {"step": "input_validation", "success": True, "status": "complete"},
                    {
                        "step": "enrichment",
                        "status": "not_performed",
                        "reason": "Aucun appel fournisseur en démonstration.",
                    },
                    {
                        "step": "icp_scoring",
                        "status": "not_performed",
                        "reason": "La qualification exige des données et des preuves vérifiées.",
                    },
                    {
                        "step": "sequence_writer",
                        "status": "not_performed",
                        "reason": "Aucun texte commercial généré ni appel LLM.",
                    },
                    {
                        "step": "send",
                        "status": "blocked",
                        "dry_run": True,
                        "reason": LIVE_SEND_UNAVAILABLE,
                    },
                    {
                        "step": "reply_classifier",
                        "status": "not_performed",
                        "reason": "Aucune réponse entrante réelle fournie.",
                    },
                ],
            }
        )
    return result
