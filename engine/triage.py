# -*- coding: utf-8 -*-
"""
Appel de triage : assemble le contexte, interroge Claude en sortie structuree,
puis applique les garde-fous. C'est la logique de WF-03, sous forme reutilisable
(n8n peut l'appeler comme micro-service, ou la porter en noeud Function).

Le modele PROPOSE un JSON de triage ; guardrails.apply_guardrails() en fait un
resultat sur. Le modele n'appelle aucun outil a effet de bord ici : il classe et
redige, le code decide de la suite.

Necessite ANTHROPIC_API_KEY pour l'appel reel. La logique des garde-fous, elle,
se teste sans cle (test_guardrails.py).
"""

from __future__ import annotations

import os
import json
from typing import Any

from guardrails import TriageContext, apply_guardrails


# Schema de sortie du triage -- impose au modele via output_config.format.
TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_ticket": {"type": "boolean"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "category": {"type": "string"},
        "subcategory": {"type": "string"},
        "priority": {"type": "string", "enum": ["p1", "p2", "p3", "p4"]},
        "autonomy_level": {"type": "string", "enum": ["N0", "N1", "N2", "N3"]},
        "confidence": {"type": "number"},
        "runbook_code": {"type": ["string", "null"]},
        "runbook_params": {"type": "object"},
        "proposed_response": {"type": ["string", "null"]},
        "escalation_reason": {"type": ["string", "null"]},
        "assignee_hint": {"type": ["string", "null"]},
        "missing_info": {"type": "array", "items": {"type": "string"}},
        "alert": {"type": ["string", "null"]},
    },
    "required": ["is_ticket", "title", "summary", "category", "subcategory",
                 "priority", "autonomy_level", "confidence"],
    "additionalProperties": False,
}


def _load_system_prompt() -> str:
    """Lit le prompt de triage depuis prompts/triage.md (bloc de code principal)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "prompts", "triage.md")
    text = open(path, encoding="utf-8").read()
    # Le premier bloc ```text ... ``` du fichier est le prompt systeme.
    start = text.index("```text") + len("```text")
    end = text.index("```", start)
    return text[start:end].strip()


def build_user_content(message_body: str, *, requester: str, channel: str,
                       history: str = "", kb: str = "", similar_cases: str = "",
                       runbooks: str = "") -> str:
    """
    Assemble le bloc de contexte. Le message utilisateur reste dans un bloc
    delimite <message> : c'est de la DONNEE, jamais concatenee au prompt systeme.
    """
    return (
        f"<demandeur>{requester}</demandeur>\n"
        f"<canal>{channel}</canal>\n"
        f"<historique>{history}</historique>\n"
        f"<kb>{kb}</kb>\n"
        f"<cas_similaires>{similar_cases}</cas_similaires>\n"
        f"<runbooks>{runbooks}</runbooks>\n\n"
        f"<message>\n{message_body}\n</message>"
    )


def run_triage(user_content: str, ctx: TriageContext, *,
               model: str | None = None) -> dict[str, Any]:
    """
    Appelle Claude, parse le JSON, applique les garde-fous.
    Retourne le triage sur, avec les champs internes _publish / adjustments / alerts.
    """
    import anthropic

    client = anthropic.Anthropic()   # lit ANTHROPIC_API_KEY / profil ant
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=_load_system_prompt(),
        messages=[{"role": "user", "content": user_content}],
        output_config={"format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}},
    )

    text = next(b.text for b in resp.content if b.type == "text")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        # Parsing defensif : un JSON casse ne doit jamais laisser passer une action.
        raw = {"is_ticket": True, "title": "triage illisible", "summary": text[:200],
               "category": "information", "subcategory": "question_procedure",
               "priority": "p3", "autonomy_level": "N3", "confidence": 0.0,
               "alert": "reponse du modele non parsable"}

    result = apply_guardrails(raw, ctx)
    out = dict(result.triage)
    out["_adjustments"] = result.adjustments
    out["_alerts"] = result.alerts
    return out


if __name__ == "__main__":
    # Verification du chargement du prompt sans appeler l'API.
    print("Prompt systeme charge :", len(_load_system_prompt()), "caracteres")
    demo = build_user_content(
        "Bonjour, pouvez-vous m'ajouter a la licence de l'outil de design ?",
        requester="Rami H. (employe) verifie", channel="Telegram / IT Support",
        runbooks="SAAS_INVITE_SEAT: inviter un utilisateur sur un outil SaaS")
    print("--- bloc de contexte ---")
    print(demo)
