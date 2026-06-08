"""Seed data for vertical saas_fr — SaaS B2B France.

Objections, sequences, pricing, and proof points for outbound to
French SaaS companies (CTOs, VPs of Engineering, founders).

Usage: call seed_saas_fr(store, playbook_id) to populate a playbook.
"""

from backend.app.rag.playbook_store import PlaybookStore

# ── Objections (common in SaaS FR outbound) ────────────

OBJECTIONS = [
    {
        "type": "objection",
        "content": "Objection: 'On n'a pas le budget en ce moment.' — Réponse: 'Je comprends. Nos clients voient un ROI dès le 2ème mois — le setup fee est couvert par le premier deal fermé. On peut démarrer avec un pilote de 4 semaines à 1 500 € pour prouver la valeur avant engagement.'",
        "tags": ["budget", "pricing", "objection"],
    },
    {
        "type": "objection",
        "content": "Objection: 'On a déjà une équipe SDR en interne.' — Réponse: 'Excellent. Nos clients nous utilisent en complément, pas en remplacement. L'IA fait le volume (sourcing, enrichissement, scoring) — vos SDR se concentrent sur les leads chauds. Résultat: 3x plus de meetings par SDR.'",
        "tags": ["competition", "internal_team", "objection"],
    },
    {
        "type": "objection",
        "content": "Objection: 'L'outbound ne marche pas dans notre secteur.' — Réponse: 'C'est précisément pour ça que nous encodons votre playbook vertical. Voici les résultats d'un client SaaS RH France: 12 meetings bookés le premier mois sur une liste de 80 comptes. Secteur différent, méthode identique — le playbook fait la différence.'",
        "tags": ["skepticism", "vertical", "objection", "proof"],
    },
    {
        "type": "objection",
        "content": "Objection: 'On préfère l'inbound.' — Réponse: 'L'inbound et l'outbound sont complémentaires. L'outbound accélère votre pipeline de 6-9 mois en allant chercher les comptes qui ne vous connaissent pas encore. On peut cibler uniquement votre ICP top 50 — zéro spray-and-pray.'",
        "tags": ["inbound", "strategy", "objection"],
    },
    {
        "type": "objection",
        "content": "Objection: 'C'est trop tôt pour nous, on n'a pas encore de product-market fit.' — Réponse: 'Justement — l'outbound vous donne un feedback rapide sur votre ICP et votre messaging. Chaque réponse (même un non) est un signal. On pivote le playbook en fonction des retours terrain.'",
        "tags": ["early_stage", "pmf", "objection"],
    },
]

# ── Sequences (multi-touch email templates) ────────────

SEQUENCES = [
    {
        "type": "sequence",
        "subtype": "email_step_1",
        "content": "Séquence email — Step 1 (intro, jour 0). Objectif: ouvrir la conversation, pas vendre. Ton: personnel, pas commercial.\n\nObjet: {{first_name}}, question sur {{company_name}}\n\nBonjour {{first_name}},\n\nJe suis tombé sur {{company_name}} via {{source}} — {{personalized_observation}}.\n\nOn aide les CTOs de SaaS B2B français comme {{reference_company}} à [résultat spécifique: ex. 'générer 15+ meetings qualifiés/mois sans SDR'].\n\nEst-ce que [problème spécifique] est un sujet pour vous en ce moment ?\n\n—\n{{sender_name}} | {{sender_company}}\n{{sender_linkedin}}\n\nPour vous désinscrire: {{optout_link}}",
        "tags": ["email", "step1", "intro", "template"],
    },
    {
        "type": "sequence",
        "subtype": "email_step_2",
        "content": "Séquence email — Step 2 (relance, jour +3). Objectif: apporter de la valeur concrète.\n\nObjet: Re: {{company_name}}\n\nBonjour {{first_name}},\n\nJe me permets de relancer — voici un cas concret qui pourrait vous parler.\n\n{{reference_company}} ({{sector}}, {{size}}) avait le même enjeu: {{pain_point}}. En 6 semaines, on a:\n- Sourcé {{N}} comptes ICP en {{vertical}}\n- Booké {{M}} meetings qualifiés\n- Pipeline généré: {{pipeline_value}}€\n\nÇa vous dirait qu'on en parle 20 minutes cette semaine ?\n\n—\n{{sender_name}} | {{sender_company}}\n\nDésinscription: {{optout_link}}",
        "tags": ["email", "step2", "followup", "proof", "template"],
    },
    {
        "type": "sequence",
        "subtype": "email_step_3",
        "content": "Séquence email — Step 3 (dernière relance, jour +7). Objectif: closure ou break propre.\n\nObjet: Dernier message — {{company_name}}\n\n{{first_name}},\n\nJe ne vous relancerai plus après ce message. Si le timing n'est pas bon, pas de souci — je reste dispo quand le sujet reviendra sur la table.\n\nEn attendant, voici notre [playbook/guide/ressource] sur {{topic}} — c'est gratuit et directement applicable.\n\nBonne semaine,\n{{sender_name}}\n\n—\nDésinscription: {{optout_link}}",
        "tags": ["email", "step3", "breakup", "template"],
    },
]

# ── Pricing ────────────────────────────────────────────

PRICING = [
    {
        "type": "pricing",
        "content": "Pricing agency (Phase 1): Setup 3 000-5 000 € (encodage playbook + infra) + récurrent 2 000-4 000 €/mois (orchestration + ops) + commission 10-15% sur deals fermés. Engagement minimum 3 mois. Premier mois de récurrent offert si setup payé upfront.",
        "tags": ["pricing", "agency", "phase1"],
    },
    {
        "type": "pricing",
        "content": "ROI typique: client SaaS B2B France, MRR 30-50k€, CAC actuel 5-8k€. Outbound via Hermès: 15-20 meetings/mois → 3-5 deals → CAC effectif 1-2k€. Payback < 1 mois.",
        "tags": ["pricing", "roi", "case"],
    },
]

# ── Proof points ───────────────────────────────────────

PROOF = [
    {
        "type": "proof",
        "content": "Case study — SaaS RH France (100 employés, Série A): 80 comptes sourcés en 2 semaines, 12 meetings bookés mois 1, 3 deals signés en 6 semaines (valeur pipeline 180k€). Coût total outbound: 3 200 €. ROI: 56x.",
        "tags": ["proof", "case_study", "saas_rh"],
    },
    {
        "type": "proof",
        "content": "Case study — SaaS DevOps France (50 employés, bootstrap): 45 comptes ICP validés, 8 meetings mois 1, 2 POC signés. Principal feedback client: 'Le scoring ICP a éliminé 60% de notre liste initiale — on ne contactait que les bons profils.'",
        "tags": ["proof", "case_study", "saas_devops"],
    },
    {
        "type": "proof",
        "content": "Métriques moyennes (tous verticaux, Phase 1): Taux d'ouverture 55-70%, taux de réponse 8-15%, taux de réunion bookée 3-5%, coût par meeting 50-80 €, coût par lead qualifié 15-30 €.",
        "tags": ["proof", "metrics", "benchmarks"],
    },
]

# ── Seed function ──────────────────────────────────────


def seed_saas_fr(store: PlaybookStore, playbook_id: str) -> int:
    """Seed a playbook with saas_fr vertical data. Returns number of chunks added."""
    chunks = []
    for obj in OBJECTIONS + SEQUENCES + PRICING + PROOF:
        chunks.append(
            {
                "chunk_type": obj["type"],
                "content": obj["content"],
                "metadata": {"tags": obj.get("tags", []), "vertical": "saas_fr"},
            }
        )

    count = 0
    for chunk in chunks:
        store.add_chunk(
            playbook_id=playbook_id,
            chunk_type=chunk["chunk_type"],
            content=chunk["content"],
            metadata=chunk["metadata"],
        )
        count += 1

    return count
