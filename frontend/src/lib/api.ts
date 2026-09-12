export interface Profile {
  company_name: string; sender_name: string; sender_email: string; offer: string;
  target: string; call_to_action: string; signature: string; privacy_url: string;
}
export type LeadStatus = "new" | "contacted" | "replied" | "meeting" | "won" | "lost" | "do_not_contact";
export interface LeadInput {
  company_name: string; siren: string; contact_name: string; email: string; website: string;
  city: string; activity: string; source: string; notes: string; qualified: boolean;
}
export interface Lead extends LeadInput {
  id: string; status: LeadStatus; created_at: string; updated_at: string; blockers: string[];
  ready: boolean; draft_count: number; sent_count: number; next_action_at: string | null;
}
export interface Draft {
  id: string; step: number; subject: string; body: string; footer: string; sent_at: string | null;
}
export interface Detail {
  lead: Lead; drafts: Draft[];
  events: { id: number; kind: string; detail: string; created_at: string }[];
}
export interface Campaign { id: string; name: string; created_at: string; leads: number; drafts: number; sent: number }
export interface Workspace { profile: Profile; leads: Lead[]; campaigns: Campaign[]; mode: string }
export interface SearchResult { results: LeadInput[]; total: number; total_pages: number; page: number }
export interface ImportResult { created: number; duplicates: number; errors: { row: number; message: string }[] }

export const emptyLead: LeadInput = { company_name: "", siren: "", contact_name: "", email: "", website: "", city: "", activity: "", source: "", notes: "", qualified: false };
export const statusLabels: Record<LeadStatus, string> = {
  new: "À prospecter", contacted: "Contacté", replied: "Réponse reçue", meeting: "Rendez-vous", won: "Client", lost: "Clôturé", do_not_contact: "Ne plus contacter",
};

let csrf: Promise<string> | undefined;
function requireLogin(): never {
  csrf = undefined;
  window.location.replace("/login");
  throw new Error("Votre session a expiré. Connectez-vous à nouveau.");
}

async function headers(method = "GET"): Promise<HeadersInit> {
  if (["GET", "HEAD"].includes(method)) return { "Content-Type": "application/json" };
  csrf ??= fetch("/api/auth/session", { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(15000) }).then(async (response) => {
    if (response.status === 401) requireLogin();
    const value = await response.json();
    if (!response.ok || typeof value.csrfToken !== "string") throw new Error("Session indisponible. Rechargez la page et réessayez.");
    return value.csrfToken;
  }).catch((error) => { csrf = undefined; throw error; });
  return { "Content-Type": "application/json", "X-CSRF-Token": await csrf };
}

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, headers: await headers(method), credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store", signal: AbortSignal.timeout(25000) });
  } catch {
    throw new Error("Connexion interrompue. Vérifiez votre connexion puis réessayez.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) requireLogin();
    if (response.status === 403) csrf = undefined;
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.slice(1).join(".")}: ${item.msg}`).join(" · ")
      : detail?.message || "Le serveur est temporairement indisponible. Réessayez plus tard.";
    throw new Error(message);
  }
  return data as T;
}

export async function download(path: string, filename: string) {
  const response = await fetch(`/api${path}`, { headers: await headers(), credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(25000) });
  if (response.status === 401) requireLogin();
  if (!response.ok) throw new Error("Export impossible. Vérifiez votre connexion et votre accès.");
  downloadBlob(await response.blob(), filename);
}

export async function logout() {
  await api("/auth/logout", "POST");
  csrf = undefined;
  window.location.replace("/login");
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
