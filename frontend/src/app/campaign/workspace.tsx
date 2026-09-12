"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ArrowDownToLine, ArrowRight, ArrowUpRight, Building2, Check, ChevronLeft, ChevronRight, CircleHelp, Clock3, Copy, FileUp, LayoutDashboard, ListFilter, Mail, Plus, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, Target, Users, X } from "lucide-react";
import { api, download, downloadBlob, logout, emptyLead, statusLabels, type Detail, type Draft, type ImportResult, type Lead, type LeadInput, type LeadStatus, type Profile, type SearchResult, type Workspace } from "@/lib/api";

type Tab = "overview" | "leads" | "search" | "campaigns" | "settings";
const tabs = [
  { id: "overview" as Tab, label: "Vue d’ensemble", icon: LayoutDashboard },
  { id: "leads" as Tab, label: "Mes prospects", icon: Users },
  { id: "search" as Tab, label: "Trouver des entreprises", icon: Search },
  { id: "campaigns" as Tab, label: "Mes séquences", icon: Mail },
  { id: "settings" as Tab, label: "Réglages Lexia", icon: Settings2 },
];
const formatDate = (value: string) => new Date(value).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
const due = (lead: Lead) => Boolean(lead.next_action_at && new Date(lead.next_action_at) <= new Date());
const inputData = (lead: Lead): LeadInput => Object.fromEntries(Object.keys(emptyLead).map((key) => [key, lead[key as keyof LeadInput]])) as unknown as LeadInput;
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Une erreur est survenue.";

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
function Empty({ title, text, children }: { title: string; text: string; children?: ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Target size={27} /></div><h3>{title}</h3><p>{text}</p>{children}</div>;
}
function Status({ lead }: { lead: Lead }) {
  return <span className={`status status-${lead.status}`}>{statusLabels[lead.status]}</span>;
}
function Modal({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className="modal" onCancel={close} onClose={close} aria-label={title}>
    <div className="modal-heading"><h2>{title}</h2><button className="icon-button" onClick={close} aria-label="Fermer"><X size={21} /></button></div>
    {children}
  </dialog>;
}

export default function CampaignWorkspace() {
  const [tab, setTab] = useState<Tab>("overview");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [newLead, setNewLead] = useState(false);
  const [campaignModal, setCampaignModal] = useState(false);
  const [importReport, setImportReport] = useState<ImportResult | null>(null);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [companyQuery, setCompanyQuery] = useState({ q: "", department: "", activity: "" });
  const [lastSearch, setLastSearch] = useState(companyQuery);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => { const next = await api<Workspace>("/workspace"); setWorkspace(next); }, []);
  useEffect(() => { let active = true; api<Workspace>("/workspace").then((data) => { if (active) setWorkspace(data); }).catch((e) => { if (active) setError(errorMessage(e)); }); return () => { active = false; }; }, []);

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  const leads = workspace?.leads || [];
  const ready = leads.filter((lead) => lead.ready && !lead.draft_count);
  const actions = leads.filter(due);
  const filtered = leads.filter((lead) => (`${lead.company_name} ${lead.contact_name} ${lead.email} ${lead.city}`).toLowerCase().includes(query.toLowerCase()) && (filter === "all" || (filter === "ready" ? lead.ready : filter === "due" ? due(lead) : lead.status === filter)));
  const configured = Boolean(workspace?.profile.sender_name && workspace.profile.sender_email && workspace.profile.offer && workspace.profile.target);

  async function openLead(id: string) {
    await perform(async () => setDetail(await api<Detail>(`/leads/${id}/detail`)));
  }
  async function importFile(file?: File) {
    if (!file) return;
    await perform(async () => {
      if (file.size > 1000000) throw new Error("Fichier limité à 1 Mo et 1 000 lignes.");
      const report = await api<ImportResult>("/leads/import", "POST", { csv_text: await file.text() });
      setImportReport(report); setNotice(`${report.created} prospect(s) ajouté(s), ${report.duplicates} doublon(s) ignoré(s).`); setTab("leads"); await refresh();
    });
  }
  async function searchCompanies(page = 1, params = companyQuery) {
    await perform(async () => {
      const result = await api<SearchResult>(`/companies/search?${new URLSearchParams({ ...params, page: String(page) })}`);
      setSearchResult(result); setLastSearch(params);
    });
  }
  function leadTable(rows: Lead[], selectable = true) {
    return <div className="table-wrap"><table><thead><tr>
      {selectable && <th className="check-cell"><input type="checkbox" aria-label="Sélectionner les prospects affichés" checked={rows.length > 0 && rows.every((l) => selected.includes(l.id))} onChange={(e) => setSelected(e.target.checked ? Array.from(new Set([...selected, ...rows.map((l) => l.id)])) : selected.filter((id) => !rows.some((l) => l.id === id)))} /></th>}
      <th>Entreprise / contact</th><th>Coordonnées</th><th>Statut</th><th>Prochaine étape</th><th><span className="sr-only">Ouvrir</span></th>
    </tr></thead><tbody>{rows.map((lead) => <tr key={lead.id}>
      {selectable && <td><input type="checkbox" aria-label={`Sélectionner ${lead.company_name}`} checked={selected.includes(lead.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, lead.id] : selected.filter((id) => id !== lead.id))} /></td>}
      <td><button className="company-button" onClick={() => openLead(lead.id)} disabled={busy}><span className="avatar">{lead.company_name.slice(0, 2).toUpperCase()}</span><span><strong>{lead.company_name}</strong><small>{lead.contact_name || lead.city || "Contact à identifier"}</small></span></button></td>
      <td><span className={!lead.email ? "muted" : ""}>{lead.email || "Email à compléter"}</span><small>{lead.source ? "Source renseignée" : "Source à renseigner"}</small></td>
      <td><Status lead={lead} /></td><td>{lead.next_action_at ? <span className={due(lead) ? "text-green" : "muted"}>{due(lead) ? "À traiter aujourd’hui" : formatDate(lead.next_action_at)}</span> : <span className="muted">{lead.ready ? lead.draft_count ? "Suivi terminé" : "Prêt pour une séquence" : lead.blockers[0]}</span>}</td>
      <td><button className="icon-button" onClick={() => openLead(lead.id)} disabled={busy} aria-label={`Ouvrir ${lead.company_name}`}><ArrowUpRight size={17} /></button></td>
    </tr>)}</tbody></table></div>;
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main">Aller au contenu</a>
    <aside className="sidebar">
      <a className="brand" href="/campaign"><span className="brand-mark">l<span>·</span></span><span>lexia<span className="brand-dot">.</span></span></a>
      <div className="workspace-label"><span className="workspace-logo">L</span><div><strong>Espace Lexia</strong><small>Acquisition B2B</small></div><span className="local-dot" /></div>
      <p className="nav-label">ESPACE DE TRAVAIL</p>
      <nav aria-label="Navigation principale">{tabs.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-item ${tab === id ? "active" : ""}`} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}><Icon size={18} /><span>{label}</span>{id === "leads" && <span className="nav-count">{leads.length}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="free-card"><div><ShieldCheck size={18} /><strong>Votre outil, vos données.</strong></div><p>Sans abonnement.<br />Enregistré dans votre espace.</p><span>ACCÈS PRIVÉ <span className="local-dot" /></span></div><a className="help-link" href="https://www.cnil.fr/fr/la-prospection-commerciale-par-courrier-electronique" target="_blank" rel="noreferrer"><CircleHelp size={16} />Les règles de la prospection<ArrowUpRight size={14} /></a><div className="profile-chip"><span className="avatar dark-avatar">LX</span><div><strong>{workspace?.profile.company_name || "Lexia"}</strong><small>Prospection · Outreached</small></div></div></div>
    </aside>
    <div className="workspace-main"><header className="topbar"><span>Acquisition <ChevronRight size={14} /> <strong>{tabs.find((t) => t.id === tab)?.label}</strong></span><span className="connection"><span className={`local-dot ${!workspace ? "offline" : ""}`} />{workspace ? "Espace connecté" : "Connexion…"}<span className="topbar-separator" /><button className="text-button" disabled={busy} onClick={() => perform(logout)}>Se déconnecter</button></span></header>
      <main id="main">
        <div className="page-title"><div><div className="eyebrow">LEXIA / PROSPECTION</div><h1>{tab === "overview" ? "Vos prochains clients commencent ici." : tabs.find((t) => t.id === tab)?.label}</h1><p>{tab === "overview" ? "Trouvez les bonnes entreprises. Engagez de vraies conversations." : tab === "leads" ? "Un fichier clair, des coordonnées vérifiées et chaque échange au même endroit." : tab === "search" ? "Construisez votre liste à partir de l’annuaire public des entreprises françaises." : tab === "campaigns" ? "Trois messages personnalisables. Vous gardez la main sur chaque envoi." : "Présentez votre offre pour préparer des messages qui vous ressemblent."}</p></div>
          <button className="btn primary" onClick={() => setNewLead(true)} disabled={busy || !workspace}><Plus size={17} />Ajouter un prospect</button></div>
        {error && <div className="alert error" role="alert"><span>{error}</span><button className="btn small" disabled={busy} onClick={() => perform(refresh)}><RefreshCw size={14} />Réessayer</button></div>}
        {notice && <div className="alert success" role="status"><Check size={17} /><span>{notice}</span><button className="icon-button" onClick={() => setNotice("")} aria-label="Masquer le message"><X size={16} /></button></div>}
        {busy && <div className="activity-bar" role="status">Opération en cours…</div>}
        {!workspace && !error && <div className="panel loading">Chargement de votre espace…</div>}

        {tab === "overview" && workspace && <>
          <section className="stats" aria-label="Statistiques">{[
            { label: "Prospects dans votre liste", value: leads.length, icon: Users, foot: `${ready.length} prêts pour une première approche` },
            { label: "Messages déclarés envoyés", value: leads.reduce((sum, lead) => sum + lead.sent_count, 0), icon: Mail, foot: "Depuis votre messagerie habituelle" },
            { label: "Rendez-vous", value: leads.filter((l) => l.status === "meeting").length, icon: Target, foot: "Conversations à transformer" },
            { label: "Actions à traiter", value: actions.length, icon: Clock3, foot: "Premiers messages et relances" },
          ].map(({ label, value, icon: Icon, foot }) => <div className="stat-card" key={label}><div>{label}<Icon size={17} /></div><strong>{value.toString().padStart(2, "0")}</strong><small>{foot}</small></div>)}</section>
          {!configured && <section className="setup-banner"><div className="setup-icon"><Sparkles size={24} /></div><div><span className="eyebrow">PREMIÈRE ÉTAPE</span><h2>Faisons les présentations.</h2><p>Votre offre, votre cible et votre signature : quelques détails pour bien commencer.</p></div><button className="btn primary" onClick={() => setTab("settings")}>Configurer Lexia<ArrowRight size={16} /></button></section>}
          <div className="overview-grid"><section className="panel"><div className="section-heading"><div><h2>Votre prochaine action</h2><p>Un peu de prospection, chaque jour.</p></div><span className="pill">{actions.length} à traiter</span></div>{actions.length ? leadTable(actions.slice(0, 6), false) : <Empty title={leads.length ? "Votre suivi est à jour" : "Votre premier prospect vous attend"} text={leads.length ? "Qualifiez vos prospects puis préparez une séquence. Les relances apparaîtront ici à leur échéance." : "Recherchez une entreprise ou importez vos contacts pour commencer à construire votre pipeline."}><button className="btn primary" onClick={() => setTab("search")}><Search size={16} />Trouver des entreprises</button><button className="btn" onClick={() => fileInput.current?.click()}><FileUp size={16} />Importer un CSV</button></Empty>}</section>
          <section className="panel workflow"><div className="section-heading"><h2>Votre méthode, en 3 étapes</h2></div>{[
            { n: "01", title: "Trouvez votre cible", text: "Un nom, un secteur ou un département. Ajoutez les entreprises pertinentes.", action: "Explorer l’annuaire", tab: "search" as Tab },
            { n: "02", title: "Préparez la conversation", text: "Vérifiez le contact et adaptez les messages à ses besoins.", action: "Voir mes prospects", tab: "leads" as Tab },
            { n: "03", title: "Gardez le lien", text: "Envoyez depuis votre boîte mail, puis suivez les réponses et les relances.", action: "Voir les séquences", tab: "campaigns" as Tab },
          ].map((item) => <div className="workflow-step" key={item.n}><span>{item.n}</span><div><h3>{item.title}</h3><p>{item.text}</p><button className="text-button" onClick={() => setTab(item.tab)}>{item.action}<ArrowRight size={14} /></button></div></div>)}</section></div>
          <div className="bottom-note"><ShieldCheck size={15} />Aucun envoi automatique. Les réponses et les rendez-vous sont renseignés par vous.</div>
        </>}

        {tab === "leads" && workspace && <section className="panel">
          <div className="section-heading"><div><h2>Votre liste <span className="pill">{leads.length}</span></h2><p>Les imports sont dédupliqués par email ou SIREN.</p></div><div className="button-row"><button className="btn" disabled={busy} onClick={() => fileInput.current?.click()}><FileUp size={16} />Importer</button><button className="btn" disabled={busy || !leads.length} onClick={() => perform(() => download("/export/leads.csv", "lexia-prospects.csv"))}><ArrowDownToLine size={16} />Exporter</button></div></div>
          <div className="list-toolbar"><label className="search-input"><Search size={17} /><input aria-label="Rechercher dans mes prospects" placeholder="Rechercher un nom, un email, une ville…" value={query} onChange={(e) => setQuery(e.target.value)} /></label><label className="filter-select"><ListFilter size={16} /><select aria-label="Filtrer les prospects" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">Tous les statuts</option><option value="ready">Prêts à contacter</option><option value="due">À traiter aujourd’hui</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
          {selected.length > 0 && <div className="selection-bar"><span>{selected.length} prospect(s) sélectionné(s)</span><button className="btn primary small" disabled={busy} onClick={() => setCampaignModal(true)}>Préparer une séquence<ArrowRight size={15} /></button><button className="text-button" onClick={() => setSelected([])}>Désélectionner</button></div>}
          {filtered.length ? leadTable(filtered) : <Empty title={leads.length ? "Aucun résultat" : "Une liste vide, un potentiel immense."} text={leads.length ? "Essayez un autre filtre ou une autre recherche." : "Ajoutez un prospect, importez un CSV ou explorez l’annuaire gratuit."}><button className="btn primary" onClick={() => setTab("search")}>Explorer l’annuaire<ArrowRight size={16} /></button></Empty>}
          {importReport && <div className="import-report" role="status"><strong>Dernier import : {importReport.created} ajouté(s) · {importReport.duplicates} doublon(s) · {importReport.errors.length} erreur(s)</strong>{importReport.errors.length > 0 && <ul>{importReport.errors.map((item) => <li key={item.row}>Ligne {item.row} : {item.message}</li>)}</ul>}</div>}
          <div className="panel-footer"><button className="text-button" onClick={() => downloadBlob(new Blob(["\ufeffcompany_name,contact_name,email,website,city,activity,source,notes\n"], { type: "text/csv;charset=utf-8" }), "modele-prospects.csv")}>Télécharger le modèle CSV<ArrowDownToLine size={14} /></button><span>CSV UTF-8 · virgule ou point-virgule · 1 000 lignes max.</span></div>
        </section>}

        {tab === "search" && <>
          <section className="panel"><div className="section-heading"><div><h2><Building2 size={20} />Annuaire des entreprises</h2><p>Entreprises françaises actives · données publiques · sans clé API</p></div><span className="status status-new">Gratuit</span></div>
            <form className="company-search" onSubmit={(e) => { e.preventDefault(); searchCompanies(); }}><Field label="Nom ou adresse"><input placeholder="Ex. : Lyon, cabinet, agence…" value={companyQuery.q} maxLength={150} onChange={(e) => setCompanyQuery({ ...companyQuery, q: e.target.value })} /></Field><Field label="Département"><input placeholder="Ex. : 75" value={companyQuery.department} maxLength={3} pattern="([0-9]{2,3}|2A|2B)?" onChange={(e) => setCompanyQuery({ ...companyQuery, department: e.target.value.toUpperCase() })} /></Field><Field label="Code NAF (secteur)"><input placeholder="Ex. : 62.01Z" value={companyQuery.activity} pattern="([0-9]{2}\.[0-9]{2}[A-Z])?" onChange={(e) => setCompanyQuery({ ...companyQuery, activity: e.target.value.toUpperCase() })} /></Field><button className="btn primary" disabled={busy || (!companyQuery.q.trim() && !companyQuery.activity)}><Search size={16} />Rechercher</button></form>
            <div className="search-explanation">La recherche textuelle porte sur le nom et l’adresse. Pour cibler un secteur, utilisez son code NAF. Les emails ne sont pas fournis : complétez-les après vérification sur le site de l’entreprise.</div>
          </section>
          {searchResult ? <section className="panel search-results"><div className="section-heading"><div><h2>{searchResult.total.toLocaleString("fr-FR")} entreprises trouvées</h2><p>Page {searchResult.page} · ajoutez uniquement celles qui correspondent à votre offre.</p></div><button className="btn" disabled={busy || !searchResult.results.length || !workspace} onClick={() => perform(async () => { const data = await api<{ created: number; duplicates: number }>("/leads/batch", "POST", { leads: searchResult.results }); setNotice(`${data.created} entreprise(s) ajoutée(s), ${data.duplicates} doublon(s) ignoré(s).`); await refresh(); })}><Plus size={16} />Ajouter cette page</button></div>
            {searchResult.results.length ? <div className="company-grid">{searchResult.results.map((company) => { const exists = leads.some((lead) => lead.siren === company.siren); return <article className="company-card" key={company.siren}><div className="company-card-top"><span className="avatar"><Building2 size={19} /></span><span className="muted">NAF {company.activity || "—"}</span></div><h3>{company.company_name}</h3><p>{company.city || "Ville non renseignée"}</p><small>SIREN {company.siren}</small><div className="company-card-bottom"><a href={company.source} target="_blank" rel="noreferrer">Fiche officielle<ArrowUpRight size={14} /></a><button className={`btn small ${exists ? "" : "primary"}`} disabled={busy || exists || !workspace} onClick={() => perform(async () => { const result = await api<{ created: boolean }>("/leads", "POST", company); setNotice(result.created ? `${company.company_name} ajouté à vos prospects.` : "Cette entreprise est déjà dans votre liste."); await refresh(); })}>{exists ? <><Check size={14} />Ajoutée</> : <><Plus size={14} />Ajouter</>}</button></div></article>; })}</div> : <Empty title="Aucune entreprise trouvée" text="Essayez un nom plus court ou retirez un filtre." />}
            <div className="pagination"><button className="btn" disabled={busy || searchResult.page <= 1} onClick={() => searchCompanies(searchResult.page - 1, lastSearch)}><ChevronLeft size={15} />Précédent</button><span>Page {searchResult.page} sur {Math.max(searchResult.total_pages, 1)}</span><button className="btn" disabled={busy || searchResult.page >= searchResult.total_pages} onClick={() => searchCompanies(searchResult.page + 1, lastSearch)}>Suivant<ChevronRight size={15} /></button></div>
          </section> : <Empty title="Commencez par votre marché" text="Recherchez un nom ou renseignez un code NAF. Affinez ensuite par département pour constituer votre liste." />}
        </>}

        {tab === "campaigns" && workspace && <>
          <section className="panel"><div className="section-heading"><div><h2>Actions du jour</h2><p>Ouvrez un prospect pour relire et préparer son prochain message.</p></div><span className="pill">{actions.length}</span></div>{actions.length ? leadTable(actions, false) : <Empty title="Rien à envoyer pour le moment" text="Sélectionnez des prospects qualifiés dans votre liste et préparez une séquence. Les relances seront disponibles 4 jours après chaque envoi déclaré."><button className="btn primary" onClick={() => setTab("leads")}>Choisir mes prospects<ArrowRight size={15} /></button></Empty>}</section>
          <section className="panel search-results"><div className="section-heading"><h2>Séquences préparées</h2><span className="pill">{workspace.campaigns.length}</span></div>{workspace.campaigns.length ? <div className="campaign-list">{workspace.campaigns.map((campaign) => <div key={campaign.id}><span className="avatar"><Mail size={19} /></span><div><strong>{campaign.name}</strong><small>{formatDate(campaign.created_at)} · {campaign.leads} prospect(s) · {campaign.drafts} messages</small></div><span className="pill">{campaign.sent} déclaré(s) envoyé(s)</span></div>)}</div> : <div className="panel-footer">Vos séquences apparaîtront ici après leur création.</div>}</section>
          <div className="bottom-note"><ShieldCheck size={15} />Une réponse, un rendez-vous ou une opposition arrête les relances. Pensez à mettre le statut à jour.</div>
        </>}
        {tab === "settings" && <SettingsForm profile={workspace?.profile} busy={busy} save={(profile) => perform(async () => { await api("/profile", "PUT", profile); await refresh(); setNotice("Réglages Lexia enregistrés. Vous pouvez préparer vos séquences."); })} backup={() => perform(() => download("/export/backup.json", "lexia-backup.json"))} />}
        <input ref={fileInput} type="file" accept=".csv,text/csv" className="sr-only" aria-label="Importer un fichier CSV" onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} />
      </main><footer className="app-footer"><span>LEXIA <span>·</span> Des relations avant des volumes.</span><span>Propulsé par Outreached</span></footer>
    </div>
    {newLead && <Modal title="Ajouter un prospect" close={() => setNewLead(false)}><LeadForm busy={busy} error={error} submit={async (data) => { await perform(async () => { const result = await api<{ created: boolean }>("/leads", "POST", data); await refresh(); setNewLead(false); setTab("leads"); setNotice(result.created ? "Prospect enregistré." : "Ce prospect existe déjà : aucun doublon créé."); }); }} /></Modal>}
    {detail && <Modal title={detail.lead.company_name} close={() => setDetail(null)}><LeadDetail key={`${detail.lead.id}-${detail.lead.updated_at}`} detail={detail} busy={busy} error={error} save={(data) => perform(async () => { await api(`/leads/${detail.lead.id}`, "PUT", data); setDetail(await api<Detail>(`/leads/${detail.lead.id}/detail`)); await refresh(); setNotice("Fiche enregistrée."); })} draftAction={(action) => perform(async () => { await action(); setDetail(await api<Detail>(`/leads/${detail.lead.id}/detail`)); await refresh(); })} createSequence={() => { setSelected([detail.lead.id]); setDetail(null); setCampaignModal(true); }} /></Modal>}
    {campaignModal && <Modal title="Préparer une séquence" close={() => setCampaignModal(false)}><CampaignForm count={selected.length} configured={configured} busy={busy} error={error} submit={(name) => perform(async () => { const result = await api<{ created: number; drafts: number; skipped: { company: string; reasons: string[] }[] }>("/campaigns", "POST", { name, lead_ids: selected }); await refresh(); setCampaignModal(false); setSelected([]); setTab("campaigns"); setNotice(`${result.drafts} brouillons préparés pour ${result.created} prospect(s). ${result.skipped.length ? result.skipped.map((s) => `${s.company} ignoré : ${s.reasons.join(", ")}`).join(" · ") : "Relisez-les avant de les envoyer."}`); })} /></Modal>}
  </div>;
}

function LeadForm({ initial, busy, error, submit }: { initial?: Lead; busy: boolean; error: string; submit: (data: LeadInput & { status?: LeadStatus }) => Promise<void> }) {
  const [value, setValue] = useState<LeadInput>(initial ? inputData(initial) : emptyLead);
  const [status, setStatus] = useState<LeadStatus>(initial?.status || "new");
  const set = (key: keyof LeadInput, next: string | boolean) => setValue({ ...value, [key]: next });
  return <form className="editor-form" onSubmit={(e) => { e.preventDefault(); submit({ ...value, ...(initial ? { status } : {}) }); }}>
    <div className="form-grid"><Field label="Entreprise *"><input required value={value.company_name} maxLength={200} onChange={(e) => set("company_name", e.target.value)} /></Field><Field label="SIREN"><input value={value.siren} pattern="[0-9]{9}|" maxLength={9} onChange={(e) => set("siren", e.target.value)} /></Field><Field label="Nom du contact"><input value={value.contact_name} maxLength={150} onChange={(e) => set("contact_name", e.target.value)} /></Field><Field label="Email professionnel"><input type="email" value={value.email} maxLength={254} onChange={(e) => set("email", e.target.value)} /></Field><Field label="Site de l’entreprise"><input value={value.website} maxLength={1000} placeholder="https://…" onChange={(e) => set("website", e.target.value)} /></Field><Field label="Ville"><input value={value.city} maxLength={150} onChange={(e) => set("city", e.target.value)} /></Field><Field label="Activité / code NAF"><input value={value.activity} maxLength={200} onChange={(e) => set("activity", e.target.value)} /></Field>{initial && <Field label="Statut"><select value={status} disabled={initial.status === "do_not_contact"} onChange={(e) => setStatus(e.target.value as LeadStatus)}>{Object.entries(statusLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></Field>}</div>
    <Field label="Source des coordonnées" hint="Indiquez où vous avez obtenu l’email (URL, rencontre, recommandation…)."><input value={value.source} maxLength={1000} placeholder="Ex. : https://entreprise.fr/contact" onChange={(e) => set("source", e.target.value)} /></Field>
    <Field label="Notes de qualification"><textarea rows={3} value={value.notes} maxLength={5000} placeholder="Besoin identifié, contexte de l’échange, prochaines étapes…" onChange={(e) => set("notes", e.target.value)} /></Field>
    <label className="qualification"><input type="checkbox" checked={value.qualified} onChange={(e) => set("qualified", e.target.checked)} /><span>J’ai vérifié les coordonnées, la pertinence professionnelle de mon offre et la possibilité de contacter cette personne.<small>Une adresse renseignée n’est pas une adresse dont la délivrabilité a été vérifiée automatiquement.</small></span></label>
    {status === "do_not_contact" && <p className="inline-warning">L’opposition bloque les messages et les relances, y compris en cas de réimport de cette adresse.</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <button className="btn primary" disabled={busy}><Check size={16} />{busy ? "Enregistrement…" : "Enregistrer le prospect"}</button>
  </form>;
}

function LeadDetail({ detail, busy, error, save, draftAction, createSequence }: { detail: Detail; busy: boolean; error: string; save: (data: LeadInput & { status?: LeadStatus }) => Promise<void>; draftAction: (action: () => Promise<void>) => Promise<void>; createSequence: () => void }) {
  const [view, setView] = useState("contact");
  return <><div className="detail-tabs">{[["contact", "Fiche prospect"], ["messages", `Messages (${detail.drafts.length})`], ["history", "Historique"]].map(([id, label]) => <button key={id} className={view === id ? "selected" : ""} onClick={() => setView(id)}>{label}</button>)}</div>
    {view === "contact" && <LeadForm initial={detail.lead} busy={busy} error={error} submit={save} />}
    {view === "messages" && <div className="drafts-section">{detail.lead.blockers.length > 0 && <div className="inline-warning">{detail.lead.blockers.join(" · ")}</div>}{detail.drafts.length ? detail.drafts.map((draft) => <DraftEditor key={draft.id} draft={draft} lead={detail.lead} busy={busy} action={draftAction} />) : <Empty title="Préparez votre premier message" text="Complétez l’email, la source et la qualification avant de créer les trois brouillons."><button className="btn primary" disabled={!detail.lead.ready || busy} onClick={createSequence}>Préparer une séquence<ArrowRight size={15} /></button></Empty>}{error && <p className="inline-error" role="alert">{error}</p>}</div>}
    {view === "history" && <div className="history">{detail.events.map((event) => <div key={event.id}><span className="local-dot" /><div><strong>{event.detail}</strong><small>{new Date(event.created_at).toLocaleString("fr-FR")}</small></div></div>)}</div>}
  </>;
}

function DraftEditor({ draft, lead, busy, action }: { draft: Draft; lead: Lead; busy: boolean; action: (fn: () => Promise<void>) => Promise<void> }) {
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [prepared, setPrepared] = useState<{ to: string; subject: string; body: string; mailto: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirmSent, setConfirmSent] = useState(false);
  const dirty = subject !== draft.subject || body !== draft.body;
  const actionable = lead.ready && due(lead) && draft.step === lead.sent_count + 1 && !draft.sent_at;
  const save = async () => { await api(`/drafts/${draft.id}`, "PUT", { subject, body }); setPrepared(null); };
  return <article className="draft-card"><div className="draft-heading"><strong>{draft.step === 1 ? "Premier contact" : `Relance ${draft.step - 1}`}</strong><span className="pill">{draft.sent_at ? `Envoi déclaré le ${formatDate(draft.sent_at)}` : draft.step === 1 ? "À votre rythme" : "4 jours après le précédent"}</span></div>
    <Field label="Objet"><input value={subject} maxLength={200} disabled={Boolean(draft.sent_at)} onChange={(e) => { setSubject(e.target.value); setPrepared(null); }} /></Field><Field label="Message"><textarea value={body} maxLength={5000} rows={8} disabled={Boolean(draft.sent_at)} onChange={(e) => { setBody(e.target.value); setPrepared(null); }} /></Field><pre className="draft-footer">{draft.footer}</pre>
    {!draft.sent_at && <><div className="button-row"><button className="btn small" disabled={busy || !dirty || !subject.trim() || !body.trim()} onClick={() => action(save)}>Enregistrer les modifications</button><button className="btn primary small" disabled={busy || !actionable || !subject.trim() || !body.trim()} onClick={() => action(async () => { if (dirty) await save(); setPrepared(await api(`/drafts/${draft.id}/prepare`, "POST")); setCopied(false); })}><Mail size={14} />Préparer l’email</button></div>
      {!actionable && <p className="muted text-small">{lead.blockers.length ? "Complétez la fiche ou vérifiez le statut du prospect." : "Disponible après le message précédent et le délai de relance."}</p>}
      {prepared && !dirty && <div className="prepared"><p>Destinataire : <strong>{prepared.to}</strong></p><p>L’envoi se fait dans votre messagerie. Ouvrir le brouillon ne l’envoie pas.</p><div className="button-row"><a className="btn primary small" href={prepared.mailto}>Ouvrir ma messagerie<ArrowUpRight size={14} /></a><button className="btn small" disabled={busy} onClick={() => action(async () => { await navigator.clipboard.writeText(`À : ${prepared.to}\nObjet : ${prepared.subject}\n\n${prepared.body}`); setCopied(true); })}><Copy size={14} />{copied ? "Copié" : "Copier le message"}</button></div><label className="qualification"><input type="checkbox" checked={confirmSent} onChange={(e) => setConfirmSent(e.target.checked)} /><span>J’ai envoyé ce message depuis ma messagerie.</span></label><button className="btn small" disabled={busy || !confirmSent} onClick={() => action(async () => { await api(`/drafts/${draft.id}/mark-sent`, "POST"); setPrepared(null); })}><Check size={14} />Enregistrer l’envoi déclaré</button></div>}
    </>}
  </article>;
}

function CampaignForm({ count, configured, busy, error, submit }: { count: number; configured: boolean; busy: boolean; error: string; submit: (name: string) => Promise<void> }) {
  const [name, setName] = useState(`Lexia · ${new Date().toLocaleDateString("fr-FR")}`);
  return <form className="editor-form" onSubmit={(e) => { e.preventDefault(); submit(name); }}><p>{count} prospect(s) sélectionné(s). Les fiches incomplètes, déjà en séquence ou clôturées seront ignorées.</p><Field label="Nom de la séquence"><input required value={name} maxLength={150} onChange={(e) => setName(e.target.value)} /></Field><div className="sequence-preview"><span>01 · Premier contact</span><ArrowRight size={14} /><span>02 · Relance à +4 jours</span><ArrowRight size={14} /><span>03 · Dernier suivi à +4 jours</span></div><p className="muted">Les textes utilisent votre offre et les informations de la fiche, sans IA payante. Relisez et personnalisez chaque brouillon.</p>{!configured && <p className="inline-warning">Complétez d’abord l’expéditeur, l’email, l’offre et la cible dans Réglages Lexia.</p>}{error && <p className="inline-error" role="alert">{error}</p>}<button className="btn primary" disabled={busy || !configured}><Sparkles size={16} />Créer les brouillons</button></form>;
}

function SettingsForm({ profile, busy, save, backup }: { profile?: Profile; busy: boolean; save: (profile: Profile) => Promise<void>; backup: () => Promise<void> }) {
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget)) as unknown as Profile; save(data); }
  return <div className="settings-grid">{profile && <section className="panel"><div className="section-heading"><div><h2>Votre identité et votre offre</h2><p>Ces informations seront utilisées dans les nouvelles séquences.</p></div></div><form key={JSON.stringify(profile)} className="editor-form" onSubmit={submit}>
    <div className="form-grid"><Field label="Entreprise *"><input name="company_name" defaultValue={profile.company_name} required maxLength={150} /></Field><Field label="Votre nom *"><input name="sender_name" defaultValue={profile.sender_name} required maxLength={150} placeholder="Prénom Nom" /></Field></div><Field label="Votre email professionnel *"><input name="sender_email" type="email" defaultValue={profile.sender_email} required maxLength={254} placeholder="vous@votre-domaine.fr" /></Field><Field label="Votre offre *" hint="Écrivez un paragraphe prêt à être inséré dans un email. Décrivez le bénéfice concret sans inventer de résultats."><textarea name="offer" defaultValue={profile.offer} required maxLength={1500} rows={4} placeholder="Avec Lexia, nous aidons… à… grâce à…" /></Field><Field label="Votre cible *"><textarea name="target" defaultValue={profile.target} required maxLength={500} rows={2} placeholder="Secteur, taille, fonction et besoin que vous pouvez résoudre." /></Field><Field label="Proposition de prochaine étape"><input name="call_to_action" defaultValue={profile.call_to_action} required maxLength={500} /></Field><Field label="Signature complémentaire"><textarea name="signature" defaultValue={profile.signature} maxLength={1000} rows={2} placeholder="Fonction, adresse de l’entreprise, téléphone…" /></Field><Field label="Page d’information sur les données personnelles"><input name="privacy_url" type="url" defaultValue={profile.privacy_url} maxLength={1000} placeholder="https://votre-site.fr/confidentialite" /></Field><button className="btn primary" disabled={busy}><Check size={16} />Enregistrer les réglages</button></form></section>}
    <div className="settings-side"><section className="panel"><div className="section-heading"><h2><ShieldCheck size={19} />Vos données vous appartiennent</h2></div><div className="panel-body"><p>Les prospects et messages sont conservés dans le stockage de votre espace, même après fermeture du navigateur.</p><p>Exportez régulièrement une sauvegarde complète. Le CSV sert à réutiliser vos contacts dans un autre outil.</p><button className="btn" disabled={busy || !profile} onClick={backup}><ArrowDownToLine size={16} />Sauvegarde complète JSON</button></div></section><section className="panel"><div className="section-heading"><h2>Accès à un espace protégé</h2></div><div className="panel-body"><p>Votre session expire après 8 heures. Déconnectez-vous lorsque vous avez terminé, particulièrement sur un ordinateur partagé.</p></div></section><section className="info-note"><h3>Une approche simple et personnelle</h3><p>L’outil prépare les messages et les relances. Il ne synchronise pas votre boîte mail et ne mesure pas les ouvertures. Renseignez les réponses et les rendez-vous dans chaque fiche.</p></section></div>
  </div>;
}
