"""Free prospecting workflow: real companies, user-owned contacts, manual outreach."""

import csv
import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote, urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.app.workspace_store import connect, get_profile

router = APIRouter(prefix="/api", tags=["Lexia"])
Status = Literal["new", "contacted", "replied", "meeting", "won", "lost", "do_not_contact"]
STOP_STATUSES = {"replied", "meeting", "won", "lost", "do_not_contact"}


def now():
    return datetime.now(UTC).isoformat()


class CleanModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def no_control_characters(cls, value):
        if isinstance(value, str) and any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("Caractères de contrôle interdits")
        return value


def validate_email(value):
    value = value.strip().lower()
    if value and not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value
    ):
        raise ValueError("Adresse email invalide")
    return value


class Profile(CleanModel):
    company_name: str = Field(default="Lexia", min_length=1, max_length=150)
    sender_name: str = Field(default="", max_length=150)
    sender_email: str = Field(default="", max_length=254)
    offer: str = Field(default="", max_length=1500)
    target: str = Field(default="", max_length=500)
    call_to_action: str = Field(
        default="Seriez-vous disponible pour un échange de 15 minutes ?",
        min_length=1,
        max_length=500,
    )
    signature: str = Field(default="", max_length=1000)
    privacy_url: str = Field(default="", max_length=1000)

    _email = field_validator("sender_email")(validate_email)

    @field_validator("privacy_url")
    @classmethod
    def valid_privacy_url(cls, value):
        if value and (
            urlparse(value).scheme not in {"https", "http"} or not urlparse(value).netloc
        ):
            raise ValueError("Lien de confidentialité invalide")
        return value


class LeadInput(CleanModel):
    company_name: str = Field(min_length=1, max_length=200)
    siren: str = Field(default="", pattern=r"^(\d{9})?$")
    contact_name: str = Field(default="", max_length=150)
    email: str = Field(default="", max_length=254)
    website: str = Field(default="", max_length=1000)
    city: str = Field(default="", max_length=150)
    activity: str = Field(default="", max_length=200)
    source: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=5000)
    qualified: bool = False

    _email = field_validator("email")(validate_email)

    @field_validator("website")
    @classmethod
    def valid_website(cls, value):
        if not value:
            return value
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value) and not value.startswith(
            ("https://", "http://")
        ):
            raise ValueError("Le site doit être une URL http(s)")
        if "://" not in value:
            value = "https://" + value
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
            raise ValueError("Le site doit être une URL http(s)")
        return value


class LeadUpdate(LeadInput):
    status: Status = "new"


class ImportRequest(CleanModel):
    csv_text: str = Field(min_length=1, max_length=1_000_000)


class BatchInput(CleanModel):
    leads: list[LeadInput] = Field(min_length=1, max_length=100)


class CampaignInput(CleanModel):
    name: str = Field(min_length=1, max_length=150)
    lead_ids: list[str] = Field(min_length=1, max_length=200)


class DraftUpdate(CleanModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("subject")
    @classmethod
    def single_line(cls, value):
        if "\n" in value or "\r" in value:
            raise ValueError("L’objet doit tenir sur une ligne")
        return value


def event(conn, lead_id, kind, detail):
    conn.execute(
        "INSERT INTO events (lead_id,kind,detail,created_at) VALUES (?,?,?,?)",
        (lead_id, kind, detail, now()),
    )


def lead_or_404(conn, lead_id):
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Prospect introuvable")
    return dict(row)


def duplicate(conn, lead, exclude=""):
    row = conn.execute(
        """SELECT id FROM leads WHERE id<>? AND (
            (email<>'' AND email=?) OR (siren<>'' AND siren=?) OR
            (lower(company_name)=lower(?) AND lower(city)=lower(?) AND email=?)) LIMIT 1""",
        (exclude, lead.email, lead.siren, lead.company_name, lead.city, lead.email),
    ).fetchone()
    return row["id"] if row else None


def is_suppressed(conn, email):
    return bool(
        email and conn.execute("SELECT 1 FROM suppressions WHERE email=?", (email,)).fetchone()
    )


def add_lead(conn, lead):
    existing = duplicate(conn, lead)
    if existing:
        return existing, False
    lead_id = str(uuid.uuid4())
    data = lead.model_dump()
    status = "do_not_contact" if is_suppressed(conn, lead.email) else "new"
    conn.execute(
        """INSERT INTO leads (id,company_name,siren,contact_name,email,website,city,activity,source,notes,qualified,status,created_at,updated_at)
        VALUES (:id,:company_name,:siren,:contact_name,:email,:website,:city,:activity,:source,:notes,:qualified,:status,:created_at,:updated_at)""",
        {**data, "id": lead_id, "status": status, "created_at": now(), "updated_at": now()},
    )
    event(conn, lead_id, "created", "Prospect ajouté")
    return lead_id, True


def eligibility(lead):
    reasons = []
    if lead["status"] in STOP_STATUSES:
        reasons.append("Suivi arrêté : réponse reçue ou prospect clôturé")
    if not lead["email"]:
        reasons.append("Email professionnel à renseigner")
    if not lead["source"]:
        reasons.append("Source des coordonnées à renseigner")
    if not lead["qualified"]:
        reasons.append("Pertinence de l’offre et coordonnées à vérifier")
    return reasons


def serialize_lead(conn, lead, drafts=None):
    data = dict(lead)
    data["qualified"] = bool(data["qualified"])
    data["blockers"] = eligibility(data)
    data["ready"] = not data["blockers"]
    drafts = (
        drafts
        if drafts is not None
        else conn.execute(
            "SELECT * FROM drafts WHERE lead_id=? ORDER BY step", (data["id"],)
        ).fetchall()
    )
    data["draft_count"] = len(drafts)
    data["sent_count"] = sum(bool(d["sent_at"]) for d in drafts)
    data["next_action_at"] = None
    if data["ready"] and drafts and data["sent_count"] < len(drafts):
        previous = next((d for d in reversed(drafts) if d["sent_at"]), None)
        data["next_action_at"] = (
            (datetime.fromisoformat(previous["sent_at"]) + timedelta(days=4)).isoformat()
            if previous
            else data["created_at"]
        )
    return data


def _drafts_by_lead(conn):
    """Fetch summary fields once, without downloading every message body."""
    by_lead = {}
    for draft in conn.execute("SELECT lead_id,step,sent_at FROM drafts ORDER BY step").fetchall():
        by_lead.setdefault(draft["lead_id"], []).append(draft)
    return by_lead


@router.get("/workspace")
def workspace():
    with connect(write=False) as conn:
        profile = get_profile(conn) or Profile().model_dump()
        by_lead = _drafts_by_lead(conn)
        leads = [
            serialize_lead(conn, row, by_lead.get(row["id"], []))
            for row in conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
        ]
        campaigns = [
            dict(row)
            for row in conn.execute("""SELECT c.id,c.name,c.created_at,COUNT(DISTINCT d.lead_id) AS leads,
            COUNT(d.id) AS drafts,SUM(CASE WHEN d.sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent
            FROM campaigns c LEFT JOIN drafts d ON d.campaign_id=c.id GROUP BY c.id ORDER BY c.created_at DESC""").fetchall()
        ]
        return {
            "profile": profile,
            "leads": leads,
            "campaigns": campaigns,
            "mode": "local_manual",
            "provider_cost_eur": 0,
        }


@router.put("/profile")
def save_profile(data: Profile):
    with connect() as conn:
        conn.execute(
            "INSERT INTO profile (id,data) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (data.model_dump_json(),),
        )
    return data


@router.post("/leads", status_code=201)
def create_lead(data: LeadInput):
    with connect() as conn:
        lead_id, created = add_lead(conn, data)
        return {"lead": serialize_lead(conn, lead_or_404(conn, lead_id)), "created": created}


@router.post("/leads/batch")
def create_batch(data: BatchInput):
    with connect() as conn:
        results = [add_lead(conn, lead) for lead in data.leads]
    return {
        "created": sum(created for _, created in results),
        "duplicates": sum(not created for _, created in results),
    }


@router.put("/leads/{lead_id}")
def update_lead(lead_id: str, data: LeadUpdate):
    with connect() as conn:
        old = lead_or_404(conn, lead_id)
        if duplicate(conn, data, lead_id):
            raise HTTPException(409, "Une fiche existe déjà pour cet email ou cette entreprise")
        if (
            old["email"] != data.email
            and conn.execute("SELECT 1 FROM drafts WHERE lead_id=?", (lead_id,)).fetchone()
        ):
            raise HTTPException(
                409,
                "Une séquence existe pour cette adresse. Créez une nouvelle fiche pour un autre contact.",
            )
        if old["status"] == "do_not_contact" and data.status != "do_not_contact":
            raise HTTPException(409, "Ce prospect est dans la liste d’opposition")
        if is_suppressed(conn, data.email) and data.status != "do_not_contact":
            raise HTTPException(409, "Cette adresse figure dans la liste d’opposition")
        fields = data.model_dump()
        # A change of recipient invalidates prior verification and drafts.
        if old["email"] != data.email:
            fields["qualified"] = False
        conn.execute(
            f"UPDATE leads SET {','.join(f'{key}=?' for key in fields)},updated_at=? WHERE id=?",
            (*fields.values(), now(), lead_id),
        )
        if data.status == "do_not_contact":
            for email in {old["email"], data.email} - {""}:
                conn.execute(
                    "INSERT INTO suppressions (email,created_at) VALUES (?,?) "
                    "ON CONFLICT(email) DO NOTHING",
                    (email, now()),
                )
        if data.status != old["status"]:
            event(conn, lead_id, "status", data.status)
        event(conn, lead_id, "updated", "Fiche mise à jour")
        return serialize_lead(conn, lead_or_404(conn, lead_id))


@router.get("/leads/{lead_id}/detail")
def lead_detail(lead_id: str):
    with connect(write=False) as conn:
        lead = serialize_lead(conn, lead_or_404(conn, lead_id))
        drafts = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM drafts WHERE lead_id=? ORDER BY step", (lead_id,)
            ).fetchall()
        ]
        events = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM events WHERE lead_id=? ORDER BY id DESC LIMIT 100", (lead_id,)
            ).fetchall()
        ]
        return {"lead": lead, "drafts": drafts, "events": events}


CSV_FIELDS = [
    "company_name",
    "siren",
    "contact_name",
    "email",
    "website",
    "city",
    "activity",
    "source",
    "notes",
]
ALIASES = {
    "entreprise": "company_name",
    "societe": "company_name",
    "société": "company_name",
    "company": "company_name",
    "nom": "contact_name",
    "contact": "contact_name",
    "ville": "city",
    "site": "website",
    "domain": "website",
    "domaine": "website",
    "activité": "activity",
    "activite": "activity",
    "e-mail": "email",
}


@router.post("/leads/import")
def import_csv(data: ImportRequest):
    text = data.csv_text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect, strict=True)
    try:
        headers = [
            ALIASES.get(h.strip().lower(), h.strip().lower()) for h in (reader.fieldnames or [])
        ]
    except csv.Error:
        raise HTTPException(
            422, "CSV illisible : vérifiez les séparateurs et les guillemets"
        ) from None
    if "company_name" not in headers or len(set(headers)) != len(headers):
        raise HTTPException(
            422,
            "Le CSV doit avoir une colonne company_name (ou entreprise) et des en-têtes uniques",
        )
    valid, errors = [], []
    try:
        for index, row in enumerate(reader, start=2):
            if index > 1001:
                raise HTTPException(422, "Import limité à 1 000 lignes par fichier")
            mapped = {
                ALIASES.get(k.strip().lower(), k.strip().lower()): v
                for k, v in row.items()
                if k is not None
            }
            if None in row:
                errors.append({"row": index, "message": "Trop de colonnes"})
                continue
            try:
                # Qualification is deliberately never imported from a third-party file.
                valid.append(LeadInput(**{key: mapped.get(key) or "" for key in CSV_FIELDS}))
            except ValidationError as exc:
                errors.append(
                    {
                        "row": index,
                        "message": "; ".join(f"{e['loc'][0]} : {e['msg']}" for e in exc.errors()),
                    }
                )
    except csv.Error:
        raise HTTPException(
            422, "CSV illisible : vérifiez les séparateurs et les guillemets"
        ) from None
    with connect() as conn:
        results = [add_lead(conn, lead) for lead in valid]
    return {
        "created": sum(created for _, created in results),
        "duplicates": sum(not created for _, created in results),
        "errors": errors,
    }


def safe_cell(value):
    text = str(value or "")
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n"))
        else text
    )


@router.get("/export/leads.csv")
def export_csv():
    output = io.StringIO()
    fields = CSV_FIELDS + ["status", "qualified", "created_at", "next_action_at"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    with connect(write=False) as conn:
        by_lead = _drafts_by_lead(conn)
        for row in conn.execute("SELECT * FROM leads ORDER BY created_at").fetchall():
            lead = serialize_lead(conn, row, by_lead.get(row["id"], []))
            writer.writerow({key: safe_cell(lead.get(key)) for key in fields})
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="lexia-prospects.csv"'},
    )


@router.get("/export/backup.json")
def export_backup():
    with connect(write=False) as conn:
        data = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            for table in ("profile", "leads", "suppressions", "campaigns", "drafts", "events")
        }
    return Response(
        json.dumps(
            {"version": 1, "exported_at": now(), "data": data}, ensure_ascii=False, indent=2
        ),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="lexia-backup.json"'},
    )


@router.get("/companies/search")
async def search_companies(
    q: str = Query(default="", max_length=150),
    department: str = Query(default="", pattern=r"^([0-9]{2,3}|2A|2B)?$"),
    activity: str = Query(default="", pattern=r"^(\d{2}\.\d{2}[A-Z])?$"),
    page: int = Query(default=1, ge=1, le=20),
):
    if not q.strip() and not activity:
        raise HTTPException(422, "Saisissez un nom ou un code NAF")
    params = {"q": q.strip(), "page": str(page), "per_page": "20", "etat_administratif": "A"}
    if department:
        params["departement"] = department
    if activity:
        params["activite_principale"] = activity
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://recherche-entreprises.api.gouv.fr/search", params=params
            )
            if response.status_code == 429:
                raise HTTPException(
                    429, "L’annuaire est très sollicité. Réessayez dans quelques secondes."
                )
            response.raise_for_status()
            payload = response.json()
        results = []
        for row in payload["results"]:
            if row.get("etat_administratif") != "A" or row.get("statut_diffusion") != "O":
                continue
            seat = row.get("siege") or {}
            # Retain business data only. Do not import directors or birth dates.
            results.append(
                LeadInput(
                    company_name=row.get("nom_complet") or row["nom_raison_sociale"],
                    siren=row["siren"],
                    city=seat.get("libelle_commune") or "",
                    activity=row.get("activite_principale") or "",
                    source=f"https://annuaire-entreprises.data.gouv.fr/entreprise/{row['siren']}",
                ).model_dump()
            )
        return {
            "results": results,
            "total": payload.get("total_results", len(results)),
            "page": page,
            "total_pages": min(payload.get("total_pages", 1), 20),
        }
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(
            502, "L’annuaire public est indisponible. Réessayez ou importez un CSV."
        ) from None


def build_sequence(lead, profile):
    greeting = f"Bonjour {lead['contact_name']}," if lead["contact_name"] else "Bonjour,"
    footer = f"{profile['sender_name']} — {profile['company_name']}\n{profile['sender_email']}"
    if profile["signature"]:
        footer += "\n" + profile["signature"]
    footer += f"\n\nSource des coordonnées : {lead['source']}"
    footer += "\nPour ne plus recevoir mes messages, répondez simplement STOP."
    footer += (
        "\nOpposition par email : mailto:"
        + quote(profile["sender_email"], safe="@")
        + "?subject=STOP"
    )
    if profile["privacy_url"]:
        footer += "\nInformations sur vos données : " + profile["privacy_url"]
    messages = [
        (
            f"{lead['company_name']} × {profile['company_name']}",
            f"{greeting}\n\nJe vous contacte au sujet de {lead['company_name']}.\n\n{profile['offer']}\n\n{profile['call_to_action']}",
        ),
        (
            f"Suite à mon message — {profile['company_name']}",
            f"{greeting}\n\nJe reviens vers vous concernant ma proposition pour {lead['company_name']}.\n\n{profile['offer']}\n\nEst-ce un sujet d’actualité pour vous ?",
        ),
        (
            f"Dernier suivi — {profile['company_name']}",
            f"{greeting}\n\nJe termine ici mon suivi concernant {lead['company_name']}. Si le sujet devient pertinent, je reste disponible pour échanger.\n\nBonne journée,",
        ),
    ]
    return [
        {
            "step": index,
            "subject": re.sub(r"[\r\n]+", " ", subject)[:200],
            "body": body,
            "footer": footer,
        }
        for index, (subject, body) in enumerate(messages, 1)
    ]


@router.post("/campaigns", status_code=201)
def create_campaign(data: CampaignInput):
    with connect() as conn:
        profile = get_profile(conn) or Profile().model_dump()
        if not all(profile[key] for key in ("sender_name", "sender_email", "offer", "target")):
            raise HTTPException(
                422,
                "Complétez votre identité, votre email, votre offre et votre cible dans les réglages Lexia",
            )
        eligible, skipped = [], []
        for lead_id in dict.fromkeys(data.lead_ids):
            lead = lead_or_404(conn, lead_id)
            reasons = eligibility(lead)
            if is_suppressed(conn, lead["email"]):
                reasons.append("Adresse dans la liste d’opposition")
            if conn.execute("SELECT 1 FROM drafts WHERE lead_id=?", (lead_id,)).fetchone():
                reasons.append("Une séquence existe déjà")
            if reasons:
                skipped.append({"company": lead["company_name"], "reasons": reasons})
            else:
                eligible.append(lead)
        if not eligible:
            raise HTTPException(
                422,
                {"message": "Aucun prospect prêt pour une nouvelle séquence", "skipped": skipped},
            )
        campaign_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO campaigns VALUES (?,?,?,?)",
            (campaign_id, data.name, json.dumps(profile), now()),
        )
        for lead in eligible:
            for draft in build_sequence(lead, profile):
                conn.execute(
                    "INSERT INTO drafts (id,campaign_id,lead_id,step,subject,body,footer) VALUES (?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        campaign_id,
                        lead["id"],
                        draft["step"],
                        draft["subject"],
                        draft["body"],
                        draft["footer"],
                    ),
                )
            event(conn, lead["id"], "drafted", data.name)
        return {
            "id": campaign_id,
            "created": len(eligible),
            "drafts": len(eligible) * 3,
            "skipped": skipped,
        }


def actionable_draft(conn, draft_id):
    row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Brouillon introuvable")
    draft = dict(row)
    lead = lead_or_404(conn, draft["lead_id"])
    if eligibility(lead) or is_suppressed(conn, lead["email"]):
        raise HTTPException(409, "Ce prospect ne peut pas être contacté. Vérifiez sa fiche.")
    if draft["sent_at"]:
        raise HTTPException(409, "Ce message est déjà marqué comme envoyé")
    earlier = conn.execute(
        "SELECT sent_at FROM drafts WHERE lead_id=? AND step<? ORDER BY step DESC",
        (lead["id"], draft["step"]),
    ).fetchall()
    if any(not step["sent_at"] for step in earlier):
        raise HTTPException(409, "Traitez d’abord le message précédent")
    if earlier and datetime.now(UTC) < datetime.fromisoformat(earlier[0]["sent_at"]) + timedelta(
        days=4
    ):
        raise HTTPException(
            409, "Cette relance sera disponible 4 jours après le dernier envoi déclaré"
        )
    return draft, lead


@router.put("/drafts/{draft_id}")
def update_draft(draft_id: str, data: DraftUpdate):
    with connect() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Brouillon introuvable")
        if row["sent_at"]:
            raise HTTPException(409, "Un message déclaré envoyé ne peut plus être modifié")
        conn.execute(
            "UPDATE drafts SET subject=?,body=? WHERE id=?", (data.subject, data.body, draft_id)
        )
    return {"saved": True}


@router.post("/drafts/{draft_id}/prepare")
def prepare_draft(draft_id: str):
    with connect(write=False) as conn:
        draft, lead = actionable_draft(conn, draft_id)
        body = draft["body"] + "\n\n" + draft["footer"]
        # Opening a mail composer never counts as an actual send.
        return {
            "to": lead["email"],
            "subject": draft["subject"],
            "body": body,
            "mailto": "mailto:"
            + quote(lead["email"], safe="@")
            + "?"
            + urlencode({"subject": draft["subject"], "body": body}, quote_via=quote),
        }


@router.post("/drafts/{draft_id}/mark-sent")
def mark_sent(draft_id: str):
    with connect() as conn:
        draft, lead = actionable_draft(conn, draft_id)
        timestamp = now()
        conn.execute("UPDATE drafts SET sent_at=? WHERE id=?", (timestamp, draft_id))
        conn.execute(
            "UPDATE leads SET status='contacted',updated_at=? WHERE id=?", (timestamp, lead["id"])
        )
        event(
            conn,
            lead["id"],
            "manual_send",
            f"Envoi du message {draft['step']} déclaré manuellement",
        )
    return {"sent_at": timestamp, "delivery_verified": False}
