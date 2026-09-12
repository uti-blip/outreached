/** Validation shared by the server-side manual workspace and its CSV importer. */
export class WorkspaceHttpError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : "Requête invalide");
    this.status = status;
    this.detail = detail;
  }
}

export type WorkspaceProfile = {
  company_name: string; sender_name: string; sender_email: string; offer: string;
  target: string; call_to_action: string; signature: string; privacy_url: string;
};
export type LeadInput = {
  company_name: string; siren: string; contact_name: string; email: string;
  website: string; city: string; activity: string; source: string; notes: string; qualified: boolean;
};
export const STATUSES = ["new", "contacted", "replied", "meeting", "won", "lost", "do_not_contact"] as const;
export type LeadStatus = typeof STATUSES[number];
export type LeadUpdate = LeadInput & { status: LeadStatus };
export const DEFAULT_PROFILE: WorkspaceProfile = {
  company_name: "Lexia", sender_name: "", sender_email: "", offer: "", target: "",
  call_to_action: "Seriez-vous disponible pour un échange de 15 minutes ?", signature: "", privacy_url: "",
};

type ValidationIssue = { type: string; loc: (string | number)[]; msg: string };
function invalid(field: string, msg: string, type = "value_error"): never {
  throw new WorkspaceHttpError(422, [{ type, loc: ["body", field], msg }] satisfies ValidationIssue[]);
}
export function cleanObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new WorkspaceHttpError(422, [{ type: "model_attributes_type", loc: ["body"], msg: "Input should be a valid dictionary or object" }]);
  }
  const data = value as Record<string, unknown>;
  const extra = Object.keys(data).find((key) => !keys.includes(key));
  if (extra !== undefined) invalid(extra, "Extra inputs are not permitted", "extra_forbidden");
  return data;
}
export function cleanString(data: Record<string, unknown>, field: string, max: number, fallback?: string, min = 0): string {
  const raw = Object.hasOwn(data, field) ? data[field] : fallback;
  if (raw === undefined) invalid(field, "Field required", "missing");
  if (typeof raw !== "string") invalid(field, "Input should be a valid string", "string_type");
  if (/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(raw)) invalid(field, "Value error, Caractères de contrôle interdits");
  const value = raw.trim();
  const length = Array.from(value).length;
  if (length < min) invalid(field, `String should have at least ${min} character${min === 1 ? "" : "s"}`, "string_too_short");
  if (length > max) invalid(field, `String should have at most ${max} characters`, "string_too_long");
  return value;
}
export function emailValue(value: string, field = "email"): string {
  value = value.trim().toLowerCase();
  if (value && !/^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}$/.test(value)) {
    invalid(field, "Value error, Adresse email invalide");
  }
  return value;
}
function websiteValue(value: string): string {
  if (!value) return value;
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value) && !value.startsWith("https://") && !value.startsWith("http://")) {
    invalid("website", "Value error, Le site doit être une URL http(s)");
  }
  if (!value.includes("://")) value = `https://${value}`;
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname || url.username || url.password) throw new Error("url");
  } catch { invalid("website", "Value error, Le site doit être une URL http(s)"); }
  return value;
}
function booleanValue(value: unknown, field: string): boolean {
  if (typeof value === "boolean") return value;
  if (value === 0 || value === 1) return Boolean(value);
  if (typeof value === "string") {
    if (["0", "off", "f", "false", "n", "no"].includes(value.toLowerCase())) return false;
    if (["1", "on", "t", "true", "y", "yes"].includes(value.toLowerCase())) return true;
  }
  invalid(field, "Input should be a valid boolean", "bool_parsing");
}
export const LEAD_FIELDS = ["company_name", "siren", "contact_name", "email", "website", "city", "activity", "source", "notes", "qualified"] as const;
export function validateLead(value: unknown): LeadInput;
export function validateLead(value: unknown, update: true): LeadUpdate;
export function validateLead(value: unknown, update = false): LeadInput | LeadUpdate {
  const data = cleanObject(value, update ? [...LEAD_FIELDS, "status"] : LEAD_FIELDS);
  const siren = cleanString(data, "siren", 9, "");
  if (!/^(\d{9})?$/.test(siren)) invalid("siren", "String should match pattern '^(\\d{9})?$'", "string_pattern_mismatch");
  const lead: LeadInput = {
    company_name: cleanString(data, "company_name", 200, undefined, 1), siren,
    contact_name: cleanString(data, "contact_name", 150, ""),
    email: emailValue(cleanString(data, "email", 254, "")),
    website: websiteValue(cleanString(data, "website", 1000, "")),
    city: cleanString(data, "city", 150, ""), activity: cleanString(data, "activity", 200, ""),
    source: cleanString(data, "source", 1000, ""), notes: cleanString(data, "notes", 5000, ""),
    qualified: booleanValue(Object.hasOwn(data, "qualified") ? data.qualified : false, "qualified"),
  };
  if (!update) return lead;
  const status = Object.hasOwn(data, "status") ? data.status : "new";
  if (!STATUSES.includes(status as LeadStatus)) invalid("status", `Input should be ${STATUSES.join(", ")}`, "literal_error");
  return { ...lead, status: status as LeadStatus };
}
export function validateProfile(value: unknown): WorkspaceProfile {
  const data = cleanObject(value, Object.keys(DEFAULT_PROFILE));
  const profile = {
    company_name: cleanString(data, "company_name", 150, DEFAULT_PROFILE.company_name, 1),
    sender_name: cleanString(data, "sender_name", 150, ""),
    sender_email: emailValue(cleanString(data, "sender_email", 254, ""), "sender_email"),
    offer: cleanString(data, "offer", 1500, ""), target: cleanString(data, "target", 500, ""),
    call_to_action: cleanString(data, "call_to_action", 500, DEFAULT_PROFILE.call_to_action, 1),
    signature: cleanString(data, "signature", 1000, ""), privacy_url: cleanString(data, "privacy_url", 1000, ""),
  };
  if (profile.privacy_url) {
    try { if (!["https:", "http:"].includes(new URL(profile.privacy_url).protocol)) throw new Error("url"); }
    catch { invalid("privacy_url", "Value error, Lien de confidentialité invalide"); }
  }
  return profile;
}
export function validateCampaign(value: unknown): { name: string; lead_ids: string[] } {
  const data = cleanObject(value, ["name", "lead_ids"]);
  const name = cleanString(data, "name", 150, undefined, 1);
  if (!Array.isArray(data.lead_ids) || data.lead_ids.length < 1 || data.lead_ids.length > 200 || data.lead_ids.some((id) => typeof id !== "string")) {
    invalid("lead_ids", "La liste doit contenir entre 1 et 200 identifiants texte");
  }
  return { name, lead_ids: (data.lead_ids as string[]).map((id) => id.trim()) };
}
export function validateDraft(value: unknown): { subject: string; body: string } {
  const data = cleanObject(value, ["subject", "body"]);
  const subject = cleanString(data, "subject", 200, undefined, 1);
  if (/[\r\n]/.test(subject)) invalid("subject", "Value error, L’objet doit tenir sur une ligne");
  return { subject, body: cleanString(data, "body", 5000, undefined, 1) };
}
export function validateImport(value: unknown): string {
  return cleanString(cleanObject(value, ["csv_text"]), "csv_text", 1_000_000, undefined, 1);
}
export function decodeBody(body: Uint8Array): unknown {
  if (body.byteLength > 1_500_000) throw new WorkspaceHttpError(413, "Requête trop volumineuse");
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body)); }
  catch { throw new WorkspaceHttpError(422, "JSON invalide"); }
}
