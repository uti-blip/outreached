const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface CampaignResult {
  campaign: string;
  leads_processed: number;
  sequences_generated: number;
  replies_classified: number;
  total_cost_eur: number;
  errors: string[];
  details: LeadDetail[];
}

export interface LeadDetail {
  company: string;
  domain: string;
  steps: StepResult[];
  skipped?: string;
  error?: string;
}

export interface StepResult {
  step: string;
  model?: string;
  score?: number;
  verdict?: string;
  intent?: string;
  routed_to?: string | null;
  dry_run?: boolean;
  external_id?: string;
  error?: string;
}

export async function runCampaign(seedList: object[]): Promise<CampaignResult> {
  const res = await fetch(`${API_URL}/api/campaign/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seed_list: seedList, campaign_name: "campaign-saas-fr-v1" }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
