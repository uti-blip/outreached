import assert from "node:assert/strict";
import { test } from "node:test";
import { encodeCsv, parseLeadCsv, safeCell } from "../src/lib/workspace-csv.ts";
import { buildSequence, createWorkspaceHandler, eligibility, serializeLead, type WorkspaceTransaction } from "../src/lib/workspace-service.ts";
import { DEFAULT_PROFILE, STATUSES, WorkspaceHttpError, validateCampaign, validateDraft, validateImport, validateLead, validateProfile } from "../src/lib/workspace-validation.ts";

const lead = { ...validateLead({ company_name: "Entreprise test", email: "person+test@example.org", source: "Salon", qualified: true }),
  id: "lead-1", status: "new" as const, created_at: "2026-09-01T12:00:00+00:00", updated_at: "2026-09-01T12:00:00+00:00" };
const profile = { ...DEFAULT_PROFILE, sender_name: "Enzo", sender_email: "contact+test@example.org", offer: "Une offre précise", target: "Équipes B2B", privacy_url: "https://example.org/privacy" };
const noDatabase: WorkspaceTransaction = async () => { throw new Error("Database must not be called"); };
const request = (data: unknown) => new TextEncoder().encode(JSON.stringify(data));

test("input validation preserves defaults, normalization, type and field bounds", () => {
  assert.equal(validateLead({ company_name: "  Acme  ", email: " X@EXAMPLE.COM " }).email, "x@example.com");
  assert.equal(validateLead({ company_name: "Acme", website: "example.org" }).website, "https://example.org");
  assert.deepEqual(validateProfile({}), DEFAULT_PROFILE);
  for (const qualified of [true, 1, "true", "yes", "on"]) assert.equal(validateLead({ company_name: "A", qualified }).qualified, true);
  for (const qualified of [false, 0, "false", "no", "off"]) assert.equal(validateLead({ company_name: "A", qualified }).qualified, false);
  for (const invalid of [
    {}, { company_name: "" }, { company_name: 123 }, { company_name: "a".repeat(201) }, { company_name: "bad\u0000" },
    { company_name: "A", qualified: "unknown" }, { company_name: "A", email: "x@example.org\r\nBcc:bad@example.org" },
    { company_name: "A", website: "javascript:alert(1)" }, { company_name: "A", website: "https://user:pass@example.org" },
    { company_name: "A", siren: "123" }, { company_name: "A", unapproved: true }, { company_name: "A", email: null },
  ]) assert.throws(() => validateLead(invalid), WorkspaceHttpError);
  assert.doesNotThrow(() => validateLead({ company_name: "😀".repeat(200) }));
  assert.throws(() => validateLead({ company_name: "😀".repeat(201) }), WorkspaceHttpError);
});

test("campaign and draft validation prevents arbitrary fields and multiline subjects", () => {
  for (const invalid of [{ name: "x", lead_ids: [] }, { name: "x", lead_ids: [123] }, { name: "x", lead_ids: Array(201).fill("id") }]) {
    assert.throws(() => validateCampaign(invalid), WorkspaceHttpError);
  }
  assert.throws(() => validateDraft({ subject: "Hello\r\nBcc:other", body: "Body" }), WorkspaceHttpError);
  assert.throws(() => validateDraft({ subject: "Hello", body: "Body", footer: "" }), WorkspaceHttpError);
  assert.throws(() => validateProfile({ sender_email: "bad" }), WorkspaceHttpError);
  assert.throws(() => validateProfile({ privacy_url: "javascript:evil" }), WorkspaceHttpError);
  assert.throws(() => validateImport({ csv_text: "x".repeat(1_000_001) }), WorkspaceHttpError);
});

test("CSV supports aliases, BOM, semicolon, quoted multiline notes and partial errors without trusting qualification", () => {
  const parsed = parseLeadCsv('\ufeffentreprise;email;ville;source;notes;qualified\n"Société; Test";ONE@example.com;Paris;Salon;"Deux\nlignes";true\nBad;invalid;;;;\n');
  assert.equal(parsed.valid.length, 1);
  assert.equal(parsed.valid[0].company_name, "Société; Test");
  assert.equal(parsed.valid[0].email, "one@example.com");
  assert.equal(parsed.valid[0].notes, "Deux\nlignes");
  assert.equal(parsed.valid[0].qualified, false);
  assert.equal(parsed.errors.length, 1);
  assert.equal(parsed.errors[0].row, 3);
  assert.equal(parseLeadCsv("company_name\temail\nAcme\tx@example.org").valid.length, 1);
  assert.equal(parseLeadCsv('company_name,notes\nAcme,a"b').valid[0].notes, 'a"b');
});

test("malformed CSV, duplicate aliases and excessive rows reject the whole import", () => {
  for (const csv of ['company_name,notes\nAcme,"unterminated', 'company_name,notes\nAcme,"quoted"trailing', 'company_name,entreprise\nA,B', "email\na@example.org", "company_name\n" + "Acme\n".repeat(1001)]) {
    assert.throws(() => parseLeadCsv(csv), WorkspaceHttpError);
  }
  assert.equal(parseLeadCsv("company_name\n" + "Acme\n".repeat(1000)).valid.length, 1000);
  const extra = parseLeadCsv("company_name,email\nAcme,x@example.org,unexpected");
  assert.equal(extra.valid.length, 0);
  assert.deepEqual(extra.errors, [{ row: 2, message: "Trop de colonnes" }]);
});

test("CSV export neutralizes spreadsheet formula cells and retains escaped fields and BOM", () => {
  for (const text of ["=cmd", " +cmd", "-cmd", "@cmd", "\tcmd", "\rline", "\nline"]) assert.ok(safeCell(text).startsWith("'"));
  const output = encodeCsv([{ company_name: 'Acme, "quoted"', notes: "two\nlines", qualified: true }]);
  assert.ok(output.startsWith("\ufeffcompany_name,"));
  assert.ok(output.includes('"Acme, ""quoted"""'));
  assert.ok(output.includes('"two\nlines"'));
  assert.ok(output.includes(",True,"));
  assert.equal(safeCell(false), "");
});

test("all stop statuses prevent readiness and any subsequent action date", () => {
  const drafts = [{ lead_id: lead.id, step: 1, sent_at: null }];
  for (const status of STATUSES.filter((status) => !["new", "contacted"].includes(status))) {
    const stopped = { ...lead, status };
    assert.ok(eligibility(stopped).length);
    assert.equal(serializeLead(stopped, drafts).ready, false);
    assert.equal(serializeLead(stopped, drafts).next_action_at, null);
  }
  assert.equal(eligibility({ ...lead, email: "", source: "", qualified: false }).length, 3);
});

test("next action uses four days after the last declared send and ends after all three drafts", () => {
  const drafts = [1, 2, 3].map((step) => ({ lead_id: lead.id, step, sent_at: step === 1 ? "2026-09-01T12:00:00+00:00" : null }));
  const result = serializeLead(lead, drafts);
  assert.equal(Date.parse(result.next_action_at!), Date.parse("2026-09-05T12:00:00Z"));
  assert.equal(result.sent_count, 1);
  assert.equal(result.draft_count, 3);
  assert.equal(serializeLead(lead, drafts.map((draft) => ({ ...draft, sent_at: "2026-09-01T12:00:00Z" }))).next_action_at, null);
});

test("three manual drafts preserve immutable STOP footer, source and encoded opposition address", () => {
  const drafts = buildSequence({ ...lead, company_name: "Acme\r\nHeader" }, profile);
  assert.deepEqual(drafts.map((draft) => draft.step), [1, 2, 3]);
  assert.ok(drafts.every((draft) => !/[\r\n]/.test(draft.subject)));
  for (const draft of drafts) {
    assert.ok(draft.footer.includes("répondez simplement STOP"));
    assert.ok(draft.footer.includes("Source des coordonnées : Salon"));
    assert.ok(draft.footer.includes("mailto:contact%2Btest@example.org?subject=STOP"));
    assert.ok(draft.footer.includes(profile.privacy_url));
  }
});

test("invalid payloads and unsupported routes return JSON errors before any database operation", async () => {
  const handle = createWorkspaceHandler(noDatabase);
  for (const [method, path, data, expected] of [
    ["POST", ["leads"], { company_name: "" }, 422],
    ["PUT", ["drafts", "d-1"], { subject: "A", body: "B", footer: "removed" }, 422],
    ["POST", ["campaigns"], { name: "A", lead_ids: [] }, 422],
    ["POST", ["leads", "batch"], {}, 404],
    ["DELETE", ["leads", "id"], {}, 404],
  ] as const) {
    const result = await handle(method, [...path], new URLSearchParams(), request(data));
    assert.equal(result.status, expected);
    assert.ok(Object.hasOwn(await result.json(), "detail"));
  }
  assert.equal((await handle("POST", ["leads"], new URLSearchParams(), request("x".repeat(1_500_000)))).status, 413);
  assert.equal((await handle("POST", ["leads"], new URLSearchParams(), new TextEncoder().encode("{"))).status, 422);
});

test("transaction business errors are translated after rollback and infrastructure failures propagate sanitized upstream", async () => {
  const businessFailure: WorkspaceTransaction = async () => { throw new WorkspaceHttpError(409, "Opposition"); };
  const response = await createWorkspaceHandler(businessFailure)("GET", ["workspace"], new URLSearchParams(), new Uint8Array());
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { detail: "Opposition" });
  await assert.rejects(createWorkspaceHandler(noDatabase)("GET", ["workspace"], new URLSearchParams(), new Uint8Array()), /Database must not be called/);
});

test("public directory uses fixed HTTPS origin, bounded timeout and only public active business fields", async () => {
  let called = false;
  const fetcher = (async (input, init) => {
    called = true;
    const url = new URL(String(input));
    assert.equal(url.origin, "https://recherche-entreprises.api.gouv.fr");
    assert.equal(url.searchParams.get("departement"), "69");
    assert.equal(url.searchParams.get("per_page"), "20");
    assert.equal(init?.redirect, "error");
    assert.ok(init?.signal);
    return Response.json({ results: [
      { siren: "123456789", nom_complet: "Société ouverte", etat_administratif: "A", statut_diffusion: "O", activite_principale: "62.01Z", siege: { libelle_commune: "LYON" }, dirigeants: [{ date_de_naissance: "1980" }] },
      { siren: "111111111", etat_administratif: "A", statut_diffusion: "P" },
      { siren: "222222222", etat_administratif: "C", statut_diffusion: "O" },
    ], total_results: 3, total_pages: 99 });
  }) as typeof fetch;
  const result = await createWorkspaceHandler(noDatabase, fetcher)("GET", ["companies", "search"], new URLSearchParams("activity=62.01Z&department=69"), new Uint8Array());
  assert.equal(result.status, 200);
  const data = await result.json();
  assert.equal(data.results.length, 1);
  assert.equal(data.results[0].city, "LYON");
  assert.equal(data.results[0].email, "");
  assert.equal(data.results[0].qualified, false);
  assert.equal(data.total_pages, 20);
  assert.ok(!JSON.stringify(data).includes("dirigeants"));
  assert.ok(called);
});

test("directory throttling, bad payloads and bad search parameters produce actionable bounded errors", async () => {
  for (const [response, expected] of [[new Response("", { status: 429 }), 429], [new Response("", { status: 500 }), 502], [Response.json({ results: [{}], total_pages: "bad" }), 502]] as const) {
    const handle = createWorkspaceHandler(noDatabase, (async () => response) as typeof fetch);
    const result = await handle("GET", ["companies", "search"], new URLSearchParams("q=Lexia"), new Uint8Array());
    assert.equal(result.status, expected);
    assert.ok((await result.json()).detail);
  }
  const handle = createWorkspaceHandler(noDatabase, (async () => { throw new Error("Must not fetch"); }) as typeof fetch);
  for (const query of ["", "q=a&page=100", "q=a&page=", "q=a&department=wrong", "q=a&activity=invalid", "q=" + "a".repeat(151)]) {
    assert.equal((await handle("GET", ["companies", "search"], new URLSearchParams(query), new Uint8Array())).status, 422);
  }
});
