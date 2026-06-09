"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import type { CampaignResult, LeadDetail } from "@/lib/api";

const DEFAULT_SEED = [
  { company_name: "Acme SaaS", domain: "acme-saas.fr", email: "cto@acme-saas.fr" },
  { company_name: "DataCorp France", domain: "datacorp.fr", email: "vpeng@datacorp.fr" },
  { company_name: "ScaleUp Lyon", domain: "scaleup-lyon.com", email: "ceo@scaleup-lyon.com" },
  { company_name: "TechFlow Paris", domain: "techflow.io", email: "cto@techflow.io" },
  { company_name: "NexGen Software", domain: "nexgen.io", email: "head@nexgen.io" },
];

export default function CampaignPage() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CampaignResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedLead, setSelectedLead] = useState<LeadDetail | null>(null);

  async function handleRun() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("http://localhost:8000/api/campaign/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seed_list: DEFAULT_SEED, campaign_name: "campaign-saas-fr-v1" }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 p-8">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Outreached</h1>
            <p className="text-zinc-400 mt-1">B2B Outbound Orchestration — Phase 1</p>
          </div>
          <Button
            onClick={handleRun}
            disabled={loading}
            size="lg"
            className="bg-emerald-600 hover:bg-emerald-500 text-white"
          >
            {loading ? "Running..." : "Run Campaign"}
          </Button>
        </div>

        {error && (
          <Card className="border-red-800 bg-red-950/50 mb-6">
            <CardContent className="pt-6">
              <p className="text-red-400">{error}</p>
            </CardContent>
          </Card>
        )}

        {/* Results Summary */}
        {result && (
          <>
            <div className="grid grid-cols-4 gap-4 mb-8">
              <Card className="border-zinc-800 bg-zinc-900">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-zinc-400">Leads</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-bold">{result.leads_processed}</p>
                </CardContent>
              </Card>
              <Card className="border-zinc-800 bg-zinc-900">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-zinc-400">Sequences</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-bold">{result.sequences_generated}</p>
                </CardContent>
              </Card>
              <Card className="border-zinc-800 bg-zinc-900">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-zinc-400">Replies</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-bold">{result.replies_classified}</p>
                </CardContent>
              </Card>
              <Card className="border-zinc-800 bg-zinc-900">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-zinc-400">Cost</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-bold text-emerald-400">
                    €{result.total_cost_eur.toFixed(4)}
                  </p>
                </CardContent>
              </Card>
            </div>

            <Separator className="my-6 bg-zinc-800" />

            {/* Lead Details */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Lead List */}
              <Card className="border-zinc-800 bg-zinc-900 lg:col-span-1">
                <CardHeader>
                  <CardTitle className="text-lg">Leads</CardTitle>
                </CardHeader>
                <CardContent>
                  <ScrollArea className="h-[400px]">
                    <div className="space-y-2">
                      {result.details.map((lead, i) => (
                        <button
                          key={i}
                          onClick={() => setSelectedLead(lead)}
                          className={`w-full text-left p-3 rounded-lg transition-colors ${
                            selectedLead === lead
                              ? "bg-zinc-800 border border-zinc-700"
                              : "hover:bg-zinc-800/50 border border-transparent"
                          }`}
                        >
                          <p className="font-medium">{lead.company}</p>
                          <div className="flex gap-1 mt-1">
                            {lead.error ? (
                              <Badge variant="destructive">Error</Badge>
                            ) : lead.skipped ? (
                              <Badge variant="secondary">Skipped</Badge>
                            ) : (
                              <Badge className="bg-emerald-900 text-emerald-300">
                                Completed
                              </Badge>
                            )}
                          </div>
                        </button>
                      ))}
                    </div>
                  </ScrollArea>
                </CardContent>
              </Card>

              {/* Lead Detail */}
              <Card className="border-zinc-800 bg-zinc-900 lg:col-span-2">
                <CardHeader>
                  <CardTitle className="text-lg">
                    {selectedLead
                      ? `${selectedLead.company} (${selectedLead.domain})`
                      : "Select a lead"}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ScrollArea className="h-[400px]">
                    {selectedLead ? (
                      <div className="space-y-4">
                        {selectedLead.error && (
                          <p className="text-red-400">{selectedLead.error}</p>
                        )}
                        {selectedLead.skipped && (
                          <p className="text-amber-400">{selectedLead.skipped}</p>
                        )}
                        {selectedLead.steps.map((step, i) => (
                          <div
                            key={i}
                            className="bg-zinc-800/50 rounded-lg p-3 border border-zinc-700"
                          >
                            <div className="flex items-center gap-2 mb-2">
                              <Badge variant="outline" className="text-xs">
                                {step.step}
                              </Badge>
                              {step.model && (
                                <span className="text-xs text-zinc-500">{step.model}</span>
                              )}
                            </div>
                            {step.score !== undefined && (
                              <p className="text-sm">
                                Score: <span className="font-bold">{step.score}</span> —{" "}
                                {step.verdict}
                              </p>
                            )}
                            {step.intent && (
                              <p className="text-sm">
                                Intent: <span className="font-bold">{step.intent}</span>
                                {step.routed_to && (
                                  <>
                                    {" "}
                                    → routed to{" "}
                                    <Badge className="bg-amber-900 text-amber-300">
                                      {step.routed_to}
                                    </Badge>
                                  </>
                                )}
                              </p>
                            )}
                            {step.external_id && (
                              <p className="text-xs text-zinc-500 mt-1">
                                ID: {step.external_id}
                              </p>
                            )}
                            {step.error && (
                              <p className="text-xs text-red-400 mt-1">{step.error}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-zinc-500 text-center py-8">
                        Click a lead to see pipeline details
                      </p>
                    )}
                  </ScrollArea>
                </CardContent>
              </Card>
            </div>
          </>
        )}

        {!result && !loading && (
          <Card className="border-zinc-800 bg-zinc-900">
            <CardContent className="pt-6 text-center py-12">
              <p className="text-zinc-500 text-lg">
                Click &ldquo;Run Campaign&rdquo; to process 5 sample leads
              </p>
              <p className="text-zinc-600 text-sm mt-2">
                Mock mode — deterministic, zero API cost
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
