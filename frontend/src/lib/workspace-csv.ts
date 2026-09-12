import { parse } from "csv-parse/sync";
import { validateLead, WorkspaceHttpError, type LeadInput } from "./workspace-validation.ts";

export const CSV_FIELDS = ["company_name", "siren", "contact_name", "email", "website", "city", "activity", "source", "notes"];
const ALIASES: Record<string, string> = {
  entreprise: "company_name", societe: "company_name", société: "company_name", company: "company_name",
  nom: "contact_name", contact: "contact_name", ville: "city", site: "website", domain: "website",
  domaine: "website", activité: "activity", activite: "activity", "e-mail": "email",
};
const headerName = (value: string) => ALIASES[value.trim().toLowerCase()] || value.trim().toLowerCase();
const unreadable = () => new WorkspaceHttpError(422, "CSV illisible : vérifiez les séparateurs et les guillemets");

function delimiter(text: string): string {
  // Inspect the header outside quoted cells, so semicolons inside company names
  // and multiline notes cannot change the delimiter for the rest of the file.
  const counts = new Map([[",", 0], [";", 0], ["\t", 0]]);
  let quoted = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') index++;
      else quoted = !quoted;
    } else if (!quoted && /[\r\n]/.test(char)) break;
    else if (!quoted && counts.has(char)) counts.set(char, counts.get(char)! + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1])[0][0];
}
export function parseLeadCsv(input: string): { valid: LeadInput[]; errors: { row: number; message: string }[] } {
  const text = input.replace(/^\uFEFF+/, "");
  const separator = delimiter(text);
  // Python's strict CSV reader permits literal quotes in an unquoted field,
  // but rejects trailing characters after a quoted field and unclosed quotes.
  // csv-parse's relax_quotes option alone would accept all three cases.
  let state: "start" | "plain" | "quoted" | "closed" = "start";
  for (const char of text) {
    if (state === "quoted") { if (char === '"') state = "closed"; }
    else if (state === "closed") {
      if (char === '"') state = "quoted";
      else if (char === separator || char === "\r" || char === "\n") state = "start";
      else throw unreadable();
    } else if (char === separator || char === "\r" || char === "\n") state = "start";
    else if (state === "start" && char === '"') state = "quoted";
    else state = "plain";
  }
  if (state === "quoted") throw unreadable();
  let records: string[][];
  try {
    records = parse(text, { delimiter: separator, relax_column_count: true, skip_empty_lines: true,
      max_record_size: 1_000_000, relax_quotes: true }) as string[][];
  } catch { throw unreadable(); }
  const headers = (records.shift() || []).map(headerName);
  if (!headers.includes("company_name") || new Set(headers).size !== headers.length) {
    throw new WorkspaceHttpError(422, "Le CSV doit avoir une colonne company_name (ou entreprise) et des en-têtes uniques");
  }
  if (records.length > 1000) throw new WorkspaceHttpError(422, "Import limité à 1 000 lignes par fichier");
  const valid: LeadInput[] = [], errors: { row: number; message: string }[] = [];
  records.forEach((row, index) => {
    if (row.length > headers.length) { errors.push({ row: index + 2, message: "Trop de colonnes" }); return; }
    const mapped = Object.fromEntries(headers.map((header, i) => [header, row[i] || ""]));
    try {
      // A CSV never attests qualification, irrespective of any imported column.
      valid.push(validateLead(Object.fromEntries(CSV_FIELDS.map((key) => [key, mapped[key] || ""]))));
    } catch (error) {
      if (!(error instanceof WorkspaceHttpError)) throw error;
      const detail = error.detail as { loc: (string | number)[]; msg: string }[];
      errors.push({ row: index + 2, message: detail.map((issue) => `${issue.loc[1]} : ${issue.msg}`).join("; ") });
    }
  });
  return { valid, errors };
}
export function safeCell(value: unknown): string {
  const text = !value ? "" : value === true ? "True" : String(value);
  return /^[=+\-@]/.test(text.trimStart()) || /^[\t\r\n]/.test(text) ? `'${text}` : text;
}
export function encodeCsv(rows: Record<string, unknown>[]): string {
  const fields = [...CSV_FIELDS, "status", "qualified", "created_at", "next_action_at"];
  const encode = (value: unknown) => {
    const cell = safeCell(value);
    return /[,"\r\n]/.test(cell) ? `"${cell.replaceAll('"', '""')}"` : cell;
  };
  return "\ufeff" + [fields.join(","), ...rows.map((row) => fields.map((field) => encode(row[field])).join(","))].join("\r\n") + "\r\n";
}
