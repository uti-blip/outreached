import { randomUUID } from "node:crypto";
import { withWorkspace } from "./workspace-db.ts";
import { encodeCsv, parseLeadCsv } from "./workspace-csv.ts";
import { allowedProxyRoute } from "./proxy-policy.ts";
import {
  DEFAULT_PROFILE, LEAD_FIELDS, WorkspaceHttpError, decodeBody, validateCampaign, validateDraft,
  validateImport, validateLead, validateProfile,
  type LeadInput, type LeadStatus, type LeadUpdate, type WorkspaceProfile,
} from "./workspace-validation.ts";

export interface WorkspaceDatabase {
  query(sql: string, values?: unknown[]): Promise<{ rows: Record<string, unknown>[]; rowCount: number | null }>;
}
export type WorkspaceTransaction = <T>(write: boolean, callback: (db: WorkspaceDatabase) => Promise<T>) => Promise<T>;
type Lead = LeadInput & { id: string; status: LeadStatus; created_at: string; updated_at: string };
type Draft = { id: string; campaign_id: string; lead_id: string; step: number; subject: string; body: string; footer: string; sent_at: string | null };
type DraftSummary = Pick<Draft, "lead_id" | "step" | "sent_at">;
const STOP_STATUSES = new Set<LeadStatus>(["replied", "meeting", "won", "lost", "do_not_contact"]);
const FOUR_DAYS = 4 * 24 * 60 * 60 * 1000;
const now = () => new Date().toISOString().replace("Z", "+00:00");
const json = (data: unknown, status = 200) => Response.json(data, { status });
// Python quote() and mailto use RFC 3986 encoding, without form '+' spaces.
const quote = (value: string) => encodeURIComponent(value).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
const emailQuote = (value: string) => quote(value).replaceAll("%40", "@");

async function rows<T>(db: WorkspaceDatabase, sql: string, values: unknown[] = []): Promise<T[]> {
  return (await db.query(sql, values)).rows as T[];
}
async function leadOr404(db: WorkspaceDatabase, id: string): Promise<Lead> {
  const [lead] = await rows<Lead>(db, "SELECT * FROM leads WHERE id=$1", [id]);
  if (!lead) throw new WorkspaceHttpError(404, "Prospect introuvable");
  return lead;
}
async function draftOr404(db: WorkspaceDatabase, id: string): Promise<Draft> {
  const [draft] = await rows<Draft>(db, "SELECT * FROM drafts WHERE id=$1", [id]);
  if (!draft) throw new WorkspaceHttpError(404, "Brouillon introuvable");
  return draft;
}
async function profile(db: WorkspaceDatabase): Promise<WorkspaceProfile> {
  const [row] = await rows<{ data: string }>(db, "SELECT data FROM profile WHERE id=1");
  return row ? JSON.parse(row.data) as WorkspaceProfile : { ...DEFAULT_PROFILE };
}
async function event(db: WorkspaceDatabase, leadId: string, kind: string, detail: string) {
  await db.query("INSERT INTO events (lead_id,kind,detail,created_at) VALUES ($1,$2,$3,$4)", [leadId, kind, detail, now()]);
}
async function suppressed(db: WorkspaceDatabase, email: string): Promise<boolean> {
  return Boolean(email && (await db.query("SELECT 1 FROM suppressions WHERE email=$1", [email])).rows.length);
}
async function duplicate(db: WorkspaceDatabase, lead: LeadInput, exclude = ""): Promise<string | undefined> {
  const [found] = await rows<{ id: string }>(db, `SELECT id FROM leads WHERE id<>$1 AND (
    (email<>'' AND email=$2) OR (siren<>'' AND siren=$3) OR
    (lower(company_name)=lower($4) AND lower(city)=lower($5) AND email=$2)) LIMIT 1`,
  [exclude, lead.email, lead.siren, lead.company_name, lead.city]);
  return found?.id;
}

export function eligibility(lead: Pick<Lead, "status" | "email" | "source" | "qualified">): string[] {
  const reasons: string[] = [];
  if (STOP_STATUSES.has(lead.status)) reasons.push("Suivi arrêté : réponse reçue ou prospect clôturé");
  if (!lead.email) reasons.push("Email professionnel à renseigner");
  if (!lead.source) reasons.push("Source des coordonnées à renseigner");
  if (!lead.qualified) reasons.push("Pertinence de l’offre et coordonnées à vérifier");
  return reasons;
}
export function serializeLead(lead: Lead, drafts: DraftSummary[]) {
  const blockers = eligibility(lead);
  const sentCount = drafts.filter((draft) => draft.sent_at).length;
  let nextActionAt: string | null = null;
  if (!blockers.length && drafts.length && sentCount < drafts.length) {
    const previous = [...drafts].reverse().find((draft) => draft.sent_at);
    nextActionAt = previous ? new Date(Date.parse(previous.sent_at!) + FOUR_DAYS).toISOString().replace("Z", "+00:00") : lead.created_at;
  }
  return { ...lead, qualified: Boolean(lead.qualified), blockers, ready: !blockers.length,
    draft_count: drafts.length, sent_count: sentCount, next_action_at: nextActionAt };
}
async function detailedLead(db: WorkspaceDatabase, lead: Lead) {
  const drafts = await rows<DraftSummary>(db, "SELECT lead_id,step,sent_at FROM drafts WHERE lead_id=$1 ORDER BY step", [lead.id]);
  return serializeLead(lead, drafts);
}
async function listedLeads(db: WorkspaceDatabase, ascending = false) {
  const drafts = await rows<DraftSummary>(db, "SELECT lead_id,step,sent_at FROM drafts ORDER BY step");
  const grouped = new Map<string, DraftSummary[]>();
  for (const draft of drafts) {
    const group = grouped.get(draft.lead_id) || [];
    group.push(draft); grouped.set(draft.lead_id, group);
  }
  const leads = await rows<Lead>(db, ascending ? "SELECT * FROM leads ORDER BY created_at" : "SELECT * FROM leads ORDER BY created_at DESC");
  return leads.map((lead) => serializeLead(lead, grouped.get(lead.id) || []));
}

async function bulkInsert(db: WorkspaceDatabase, table: "leads" | "events" | "drafts", columns: string[], values: unknown[][]) {
  if (!values.length) return;
  const params: unknown[] = [];
  const placeholders = values.map((row) => `(${row.map((value) => { params.push(value); return `$${params.length}`; }).join(",")})`);
  // Table and column names only come from constant call sites below.
  await db.query(`INSERT INTO ${table} (${columns.join(",")}) VALUES ${placeholders.join(",")}`, params);
}
async function addLeads(db: WorkspaceDatabase, leads: LeadInput[]): Promise<{ id: string; created: boolean }[]> {
  if (!leads.length) return [];
  if (leads.length > 1000) throw new WorkspaceHttpError(422, "Import limité à 1 000 lignes par fichier");
  const params: unknown[] = [];
  const values = leads.map((lead, i) => `(${[String(i), lead.company_name, lead.city, lead.email, lead.siren].map((value) => {
    params.push(value); return `$${params.length}::text`;
  }).join(",")})`);
  type Match = { position: number; company_key: string; city_key: string; duplicate_id: string | null; suppressed: boolean };
  const matches = await rows<Match>(db, `WITH incoming (position,company_name,city,email,siren) AS (VALUES ${values.join(",")})
    SELECT i.position::integer AS position,lower(i.company_name) AS company_key,lower(i.city) AS city_key,
      (SELECT l.id FROM leads l WHERE (l.email<>'' AND l.email=i.email) OR (l.siren<>'' AND l.siren=i.siren) OR
      (lower(l.company_name)=lower(i.company_name) AND lower(l.city)=lower(i.city) AND l.email=i.email) LIMIT 1) AS duplicate_id,
      EXISTS(SELECT 1 FROM suppressions s WHERE i.email<>'' AND s.email=i.email) AS suppressed
    FROM incoming i ORDER BY i.position::integer`, params);
  const emails = new Map<string, string>(), sirens = new Map<string, string>(), companies = new Map<string, string>();
  const leadRows: unknown[][] = [], eventRows: unknown[][] = [], results: { id: string; created: boolean }[] = [];
  const columns = ["id", ...LEAD_FIELDS, "status", "created_at", "updated_at"];
  leads.forEach((lead, index) => {
    const match = matches[index];
    if (!match || match.position !== index) throw new Error("Workspace import result mismatch");
    const companyKey = JSON.stringify([match.company_key, match.city_key, lead.email]);
    const existing = match.duplicate_id || (lead.email && emails.get(lead.email)) || (lead.siren && sirens.get(lead.siren)) || companies.get(companyKey);
    if (existing) { results.push({ id: existing, created: false }); return; }
    const id = randomUUID(), timestamp = now();
    const record = { ...lead, id, status: match.suppressed ? "do_not_contact" : "new", created_at: timestamp, updated_at: timestamp } as Record<string, unknown>;
    leadRows.push(columns.map((column) => record[column]));
    eventRows.push([id, "created", "Prospect ajouté", timestamp]);
    if (lead.email) emails.set(lead.email, id);
    if (lead.siren) sirens.set(lead.siren, id);
    companies.set(companyKey, id);
    results.push({ id, created: true });
  });
  await bulkInsert(db, "leads", columns, leadRows);
  await bulkInsert(db, "events", ["lead_id", "kind", "detail", "created_at"], eventRows);
  return results;
}

export function buildSequence(lead: LeadInput, data: WorkspaceProfile) {
  const greeting = lead.contact_name ? `Bonjour ${lead.contact_name},` : "Bonjour,";
  let footer = `${data.sender_name} — ${data.company_name}\n${data.sender_email}`;
  if (data.signature) footer += `\n${data.signature}`;
  footer += `\n\nSource des coordonnées : ${lead.source}`;
  footer += "\nPour ne plus recevoir mes messages, répondez simplement STOP.";
  footer += `\nOpposition par email : mailto:${emailQuote(data.sender_email)}?subject=STOP`;
  if (data.privacy_url) footer += `\nInformations sur vos données : ${data.privacy_url}`;
  const messages = [
    [`${lead.company_name} × ${data.company_name}`, `${greeting}\n\nJe vous contacte au sujet de ${lead.company_name}.\n\n${data.offer}\n\n${data.call_to_action}`],
    [`Suite à mon message — ${data.company_name}`, `${greeting}\n\nJe reviens vers vous concernant ma proposition pour ${lead.company_name}.\n\n${data.offer}\n\nEst-ce un sujet d’actualité pour vous ?`],
    [`Dernier suivi — ${data.company_name}`, `${greeting}\n\nJe termine ici mon suivi concernant ${lead.company_name}. Si le sujet devient pertinent, je reste disponible pour échanger.\n\nBonne journée,`],
  ];
  return messages.map(([subject, body], index) => ({ step: index + 1, subject: Array.from(subject.replace(/[\r\n]+/g, " ")).slice(0, 200).join(""), body, footer }));
}
async function createCampaign(db: WorkspaceDatabase, data: { name: string; lead_ids: string[] }) {
  const savedProfile = await profile(db);
  if (![savedProfile.sender_name, savedProfile.sender_email, savedProfile.offer, savedProfile.target].every(Boolean)) {
    throw new WorkspaceHttpError(422, "Complétez votre identité, votre email, votre offre et votre cible dans les réglages Lexia");
  }
  const ids = [...new Set(data.lead_ids)];
  const selected = await rows<Lead & { suppressed: boolean; has_drafts: boolean }>(db,
    `SELECT l.*,EXISTS(SELECT 1 FROM suppressions s WHERE s.email=l.email AND l.email<>'') AS suppressed,
    EXISTS(SELECT 1 FROM drafts d WHERE d.lead_id=l.id) AS has_drafts FROM leads l WHERE l.id=ANY($1::text[])`, [ids]);
  const byId = new Map(selected.map((lead) => [lead.id, lead]));
  const eligible: Lead[] = [], skipped: { company: string; reasons: string[] }[] = [];
  for (const id of ids) {
    const lead = byId.get(id);
    if (!lead) throw new WorkspaceHttpError(404, "Prospect introuvable");
    const reasons = eligibility(lead);
    if (lead.suppressed) reasons.push("Adresse dans la liste d’opposition");
    if (lead.has_drafts) reasons.push("Une séquence existe déjà");
    if (reasons.length) skipped.push({ company: lead.company_name, reasons });
    else eligible.push(lead);
  }
  if (!eligible.length) throw new WorkspaceHttpError(422, { message: "Aucun prospect prêt pour une nouvelle séquence", skipped });
  const campaignId = randomUUID();
  await db.query("INSERT INTO campaigns (id,name,profile,created_at) VALUES ($1,$2,$3,$4)", [campaignId, data.name, JSON.stringify(savedProfile), now()]);
  const draftRows: unknown[][] = [], eventRows: unknown[][] = [];
  for (const lead of eligible) {
    for (const draft of buildSequence(lead, savedProfile)) {
      draftRows.push([randomUUID(), campaignId, lead.id, draft.step, draft.subject, draft.body, draft.footer]);
    }
    eventRows.push([lead.id, "drafted", data.name, now()]);
  }
  await bulkInsert(db, "drafts", ["id", "campaign_id", "lead_id", "step", "subject", "body", "footer"], draftRows);
  await bulkInsert(db, "events", ["lead_id", "kind", "detail", "created_at"], eventRows);
  return { id: campaignId, created: eligible.length, drafts: eligible.length * 3, skipped };
}
async function updateLead(db: WorkspaceDatabase, id: string, data: LeadUpdate) {
  const old = await leadOr404(db, id);
  if (await duplicate(db, data, id)) throw new WorkspaceHttpError(409, "Une fiche existe déjà pour cet email ou cette entreprise");
  if (old.email !== data.email && (await db.query("SELECT 1 FROM drafts WHERE lead_id=$1", [id])).rows.length) {
    throw new WorkspaceHttpError(409, "Une séquence existe pour cette adresse. Créez une nouvelle fiche pour un autre contact.");
  }
  if (old.status === "do_not_contact" && data.status !== "do_not_contact") throw new WorkspaceHttpError(409, "Ce prospect est dans la liste d’opposition");
  if (await suppressed(db, data.email) && data.status !== "do_not_contact") throw new WorkspaceHttpError(409, "Cette adresse figure dans la liste d’opposition");
  const fields = { ...data, qualified: old.email === data.email ? data.qualified : false };
  const keys = [...LEAD_FIELDS, "status"] as const;
  await db.query(`UPDATE leads SET ${keys.map((key, i) => `${key}=$${i + 1}`).join(",")},updated_at=$${keys.length + 1} WHERE id=$${keys.length + 2}`,
    [...keys.map((key) => fields[key]), now(), id]);
  if (data.status === "do_not_contact") {
    for (const email of new Set([old.email, data.email].filter(Boolean))) {
      await db.query("INSERT INTO suppressions (email,created_at) VALUES ($1,$2) ON CONFLICT(email) DO NOTHING", [email, now()]);
    }
  }
  if (data.status !== old.status) await event(db, id, "status", data.status);
  await event(db, id, "updated", "Fiche mise à jour");
  return detailedLead(db, await leadOr404(db, id));
}
async function actionableDraft(db: WorkspaceDatabase, id: string) {
  const draft = await draftOr404(db, id), lead = await leadOr404(db, draft.lead_id);
  if (eligibility(lead).length || await suppressed(db, lead.email)) throw new WorkspaceHttpError(409, "Ce prospect ne peut pas être contacté. Vérifiez sa fiche.");
  if (draft.sent_at) throw new WorkspaceHttpError(409, "Ce message est déjà marqué comme envoyé");
  const earlier = await rows<{ sent_at: string | null }>(db, "SELECT sent_at FROM drafts WHERE lead_id=$1 AND step<$2 ORDER BY step DESC", [lead.id, draft.step]);
  if (earlier.some((step) => !step.sent_at)) throw new WorkspaceHttpError(409, "Traitez d’abord le message précédent");
  if (earlier.length && Date.now() < Date.parse(earlier[0].sent_at!) + FOUR_DAYS) {
    throw new WorkspaceHttpError(409, "Cette relance sera disponible 4 jours après le dernier envoi déclaré");
  }
  return { draft, lead };
}

async function searchCompanies(search: URLSearchParams, fetcher: typeof fetch) {
  const q = search.get("q") || "", department = search.get("department") || "", activity = search.get("activity") || "";
  const rawPage = search.get("page") ?? "1", page = Number(rawPage);
  if (Array.from(q).length > 150 || !/^([0-9]{2,3}|2A|2B)?$/.test(department) || !/^(\d{2}\.\d{2}[A-Z])?$/.test(activity)
      || !/^[+]?\d+(?:\.0+)?$/.test(rawPage.trim()) || !Number.isInteger(page) || page < 1 || page > 20) {
    throw new WorkspaceHttpError(422, "Paramètres de recherche invalides");
  }
  if (!q.trim() && !activity) throw new WorkspaceHttpError(422, "Saisissez un nom ou un code NAF");
  const params = new URLSearchParams({ q: q.trim(), page: String(page), per_page: "20", etat_administratif: "A" });
  if (department) params.set("departement", department);
  if (activity) params.set("activite_principale", activity);
  try {
    const response = await fetcher(`https://recherche-entreprises.api.gouv.fr/search?${params}`, {
      signal: AbortSignal.timeout(15_000), redirect: "error", cache: "no-store",
    });
    if (response.status === 429) throw new WorkspaceHttpError(429, "L’annuaire est très sollicité. Réessayez dans quelques secondes.");
    if (!response.ok) throw new Error("Directory unavailable");
    const payload: unknown = await response.json();
    if (!payload || typeof payload !== "object" || !Array.isArray((payload as Record<string, unknown>).results)) throw new Error("Directory response invalid");
    const data = payload as { results: Record<string, unknown>[]; total_results?: number; total_pages?: number };
    const results: LeadInput[] = [];
    for (const row of data.results) {
      if (!row || typeof row !== "object") throw new Error("Directory row invalid");
      if (row.etat_administratif !== "A" || row.statut_diffusion !== "O") continue;
      const seat = (row.siege || {}) as Record<string, unknown>;
      results.push(validateLead({ company_name: row.nom_complet || row.nom_raison_sociale, siren: row.siren,
        city: seat.libelle_commune || "", activity: row.activite_principale || "",
        source: `https://annuaire-entreprises.data.gouv.fr/entreprise/${row.siren}` }));
    }
    if ((data.total_results !== undefined && !Number.isFinite(data.total_results)) || (data.total_pages !== undefined && !Number.isFinite(data.total_pages))) throw new Error("Directory totals invalid");
    return { results, total: data.total_results ?? results.length, page, total_pages: Math.min(data.total_pages ?? 1, 20) };
  } catch (error) {
    if (error instanceof WorkspaceHttpError && error.status === 429) throw error;
    throw new WorkspaceHttpError(502, "L’annuaire public est indisponible. Réessayez ou importez un CSV.");
  }
}

/** The auth/CSRF route wrapper calls this only after validating the request. */
export function createWorkspaceHandler(transaction: WorkspaceTransaction, fetcher: typeof fetch = fetch) {
  return async (method: string, path: string[], search: URLSearchParams, body: Uint8Array): Promise<Response> => {
    try {
      if (!allowedProxyRoute(method, path)) throw new WorkspaceHttpError(404, "Route introuvable");
      const route = path.join("/");
      if (method === "GET" && route === "companies/search") return json(await searchCompanies(search, fetcher));
      if (method === "GET" && route === "workspace") return await transaction(false, async (db) => {
        const savedProfile = await profile(db), leads = await listedLeads(db);
        const campaigns = await rows<{ id: string; name: string; created_at: string; leads: string; drafts: string; sent: string }>(db,
          `SELECT c.id,c.name,c.created_at,COUNT(DISTINCT d.lead_id) AS leads,COUNT(d.id) AS drafts,
          SUM(CASE WHEN d.sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent
          FROM campaigns c LEFT JOIN drafts d ON d.campaign_id=c.id GROUP BY c.id ORDER BY c.created_at DESC`);
        return json({ profile: savedProfile, leads, campaigns: campaigns.map((item) => ({ ...item, leads: Number(item.leads), drafts: Number(item.drafts), sent: Number(item.sent) })), mode: "local_manual", provider_cost_eur: 0 });
      });
      if (method === "PUT" && route === "profile") {
        const data = validateProfile(decodeBody(body));
        return await transaction(true, async (db) => {
          await db.query("INSERT INTO profile (id,data) VALUES (1,$1) ON CONFLICT(id) DO UPDATE SET data=excluded.data", [JSON.stringify(data)]);
          return json(data);
        });
      }
      if (method === "POST" && route === "leads") {
        const data = validateLead(decodeBody(body));
        return await transaction(true, async (db) => {
          const [result] = await addLeads(db, [data]);
          return json({ lead: await detailedLead(db, await leadOr404(db, result.id)), created: result.created }, 201);
        });
      }
      if (method === "POST" && route === "leads/import") {
        const parsed = parseLeadCsv(validateImport(decodeBody(body)));
        return await transaction(true, async (db) => {
          const added = await addLeads(db, parsed.valid);
          return json({ created: added.filter((item) => item.created).length, duplicates: added.filter((item) => !item.created).length, errors: parsed.errors });
        });
      }
      if (method === "PUT" && path[0] === "leads") {
        const data = validateLead(decodeBody(body), true);
        return await transaction(true, async (db) => json(await updateLead(db, path[1], data)));
      }
      if (method === "GET" && path[0] === "leads") return await transaction(false, async (db) => {
        const lead = await leadOr404(db, path[1]);
        const drafts = await rows<Draft>(db, "SELECT * FROM drafts WHERE lead_id=$1 ORDER BY step", [lead.id]);
        const events = await rows<{ id: string }>(db, "SELECT * FROM events WHERE lead_id=$1 ORDER BY id DESC LIMIT 100", [lead.id]);
        return json({ lead: serializeLead(lead, drafts), drafts, events: events.map((item) => ({ ...item, id: Number(item.id) })) });
      });
      if (method === "GET" && route === "export/leads.csv") return await transaction(false, async (db) => new Response(encodeCsv(await listedLeads(db, true)), {
        headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="lexia-prospects.csv"' },
      }));
      if (method === "GET" && route === "export/backup.json") return await transaction(false, async (db) => {
        const data: Record<string, Record<string, unknown>[]> = {};
        for (const table of ["profile", "leads", "suppressions", "campaigns", "drafts", "events"]) {
          data[table] = (await db.query(`SELECT * FROM ${table}`)).rows.map((row) => table === "events" ? { ...row, id: Number(row.id) } : row);
        }
        return new Response(JSON.stringify({ version: 1, exported_at: now(), data }, null, 2), {
          headers: { "Content-Type": "application/json", "Content-Disposition": 'attachment; filename="lexia-backup.json"' },
        });
      });
      if (method === "POST" && route === "campaigns") {
        const data = validateCampaign(decodeBody(body));
        return await transaction(true, async (db) => json(await createCampaign(db, data), 201));
      }
      if (method === "PUT" && path[0] === "drafts") {
        const data = validateDraft(decodeBody(body));
        return await transaction(true, async (db) => {
          const draft = await draftOr404(db, path[1]);
          if (draft.sent_at) throw new WorkspaceHttpError(409, "Un message déclaré envoyé ne peut plus être modifié");
          await db.query("UPDATE drafts SET subject=$1,body=$2 WHERE id=$3", [data.subject, data.body, draft.id]);
          return json({ saved: true });
        });
      }
      if (method === "POST" && path[0] === "drafts" && path[2] === "prepare") return await transaction(false, async (db) => {
        const { draft, lead } = await actionableDraft(db, path[1]);
        const message = `${draft.body}\n\n${draft.footer}`;
        return json({ to: lead.email, subject: draft.subject, body: message,
          mailto: `mailto:${emailQuote(lead.email)}?subject=${quote(draft.subject)}&body=${quote(message)}` });
      });
      if (method === "POST" && path[0] === "drafts" && path[2] === "mark-sent") return await transaction(true, async (db) => {
        const { draft, lead } = await actionableDraft(db, path[1]), timestamp = now();
        await db.query("UPDATE drafts SET sent_at=$1 WHERE id=$2", [timestamp, draft.id]);
        await db.query("UPDATE leads SET status='contacted',updated_at=$1 WHERE id=$2", [timestamp, lead.id]);
        await event(db, lead.id, "manual_send", `Envoi du message ${draft.step} déclaré manuellement`);
        return json({ sent_at: timestamp, delivery_verified: false });
      });
      throw new WorkspaceHttpError(404, "Route introuvable");
    } catch (error) {
      if (error instanceof WorkspaceHttpError) return json({ detail: error.detail }, error.status);
      throw error;
    }
  };
}

export const handleWorkspaceRequest = createWorkspaceHandler(withWorkspace);
