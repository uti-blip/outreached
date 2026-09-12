"""Cost tracker — async logger for agent_runs table.

Logs reported tokens, estimated cost and latency per agent run. Persistence is
awaited and failures are surfaced; this is not a provider billing reconciliation.
"""

import uuid

from backend.app.db.supabase import _get_sqlite, get_supabase


async def log_agent_run(
    tenant_id: str,
    agent_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_eur: float,
    latency_ms: int,
    campaign_id: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Log an agent run and require durable success from the selected store."""
    import json

    run_id = str(uuid.uuid4())

    # A configured store must not fail over silently to an unrelated database.
    client = get_supabase()
    if client:
        client.table("agent_runs").insert(
            {
                "id": run_id,
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "agent_type": agent_type,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_eur": cost_eur,
                "latency_ms": latency_ms,
                "metadata": metadata or {},
            }
        ).execute()
        return run_id

    # SQLite fallback
    conn = _get_sqlite()
    try:
        conn.execute(
            """INSERT INTO agent_runs (id, tenant_id, campaign_id, agent_type, model,
               input_tokens, output_tokens, cost_eur, latency_ms, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                tenant_id,
                campaign_id,
                agent_type,
                model,
                input_tokens,
                output_tokens,
                cost_eur,
                latency_ms,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return run_id


def get_campaign_costs(tenant_id: str, campaign_id: str) -> dict:
    """Return local development costs without reading from a different store."""
    if get_supabase() is not None:
        raise NotImplementedError("Supabase campaign cost aggregation is not implemented")
    conn = _get_sqlite()
    try:
        row = conn.execute(
            """SELECT COUNT(*) as runs,
                      SUM(input_tokens) as total_input,
                      SUM(output_tokens) as total_output,
                      SUM(cost_eur) as total_cost_eur,
                      AVG(latency_ms) as avg_latency_ms
               FROM agent_runs
               WHERE tenant_id = ? AND campaign_id = ?""",
            (tenant_id, campaign_id),
        ).fetchone()
        return {
            "total_runs": row["runs"] or 0,
            "total_input_tokens": row["total_input"] or 0,
            "total_output_tokens": row["total_output"] or 0,
            "total_cost_eur": round(row["total_cost_eur"] or 0, 4),
            "avg_latency_ms": round(row["avg_latency_ms"] or 0, 1),
        }
    finally:
        conn.close()
