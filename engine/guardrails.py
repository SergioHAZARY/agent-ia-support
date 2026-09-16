# -*- coding: utf-8 -*-
"""
Garde-fous appliques APRES la reponse du modele (regle 5 du document).

Le modele propose, le code dispose. Aucune de ces verifications ne depend de
la bonne volonte du modele : elles s'executent en Python, sur le JSON de triage
renvoye par Claude, avant qu'aucune action ne soit entreprise.

Ce module n'a AUCUNE dependance externe : il se teste sans cle API ni reseau
(voir test_guardrails.py). C'est volontaire -- c'est la brique de securite, elle
doit etre verifiable de facon isolee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# Contexte fourni par WF-02 : ce que le code SAIT, independamment du modele.
# --------------------------------------------------------------------------- #
@dataclass
class Identity:
    verified: bool = False
    role: str = "employe"          # employe | manager | it | admin
    email: str | None = None


@dataclass
class Runbook:
    code: str
    autonomy_level: str            # N0 | N1 | N2
    allowed_roles: tuple[str, ...] = ("employe",)
    manager_approval: bool = False


@dataclass
class TriageContext:
    max_autonomy: str = "N3"       # plafond de la societe : N1 | N2 | N3
    observation_only: bool = False # palier P0 : on ne publie rien
    identity: Identity = field(default_factory=Identity)
    runbooks: dict[str, Runbook] = field(default_factory=dict)   # code -> Runbook
    taxonomy_categories: frozenset[str] = frozenset()
    confidence_floor: float = 0.7


# Ordre des niveaux, pour comparer un niveau propose au plafond.
_ORDER = {"N0": 0, "N1": 1, "N2": 2, "N3": 3}
# Ce qu'une societe a le droit de faire selon son plafond.
_ALLOWED_BY_CAP = {
    "N3": {"N0", "N1", "N2", "N3"},  # P1+ : tous niveaux, l'agent choisit
    "N2": {"N0", "N2", "N3"},        # P2 : repond et guide
    "N1": {"N0", "N1", "N3"},        # P3 : execute
    "N0": {"N0", "N3"},              # P0 : repond seulement
}


# --------------------------------------------------------------------------- #
# Les interdits permanents (section 10). Filet par mots-cles : NON la protection
# principale (ca, ce sont allowed_roles et la liste des runbooks disponibles),
# mais une barriere supplementaire qui ne bouge jamais.
# --------------------------------------------------------------------------- #
_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("transmission de secret", re.compile(
        r"\b(2fa|otp|code de v[eé]rification|mot de passe|password|token|mfa)\b.*"
        r"\b(donn\w*|envoi\w*|transmet\w*|transmett\w*|partag\w*|besoin|c'est quoi|quel est)\b"
        r"|\b(donn\w*|envoi\w*|transmet\w*|transmett\w*|partag\w*)\b.*"
        r"\b(2fa|otp|mot de passe|password|token|mfa)\b",
        re.I)),
    ("compte a privileges", re.compile(
        r"\b(domain admins?|enterprise admins?|schema admins?|global admin|root|sudo|"
        r"administrateur du domaine|admin du domaine)\b", re.I)),
    ("suppression de donnees", re.compile(
        r"\b(supprim\w*|efface\w*|delete|purge\w*)\b.*"
        r"\b(compte|bo[iî]te|mail|donn[ée]es|fichier|utilisateur)\b", re.I)),
    ("modification de regle de securite", re.compile(
        r"\b(pare-feu|firewall|politique mfa|conditional access|antivirus|gpo)\b", re.I)),
    ("donnees RH ou financieres", re.compile(
        r"\b(paie|salaire|bulletin|rh\b|contrat de travail|facture|comptabilit[ée])\b", re.I)),
    ("action sur la production", re.compile(
        r"\b(production|prod\b|serveur de prod)\b", re.I)),
    ("prise en main a distance", re.compile(
        r"\b(rdp|bureau [àa] distance|anydesk|teamviewer|prise en main)\b", re.I)),
]


def check_forbidden(text: str) -> str | None:
    """Retourne la raison si le texte touche un interdit permanent, sinon None."""
    for reason, pat in _FORBIDDEN_PATTERNS:
        if pat.search(text or ""):
            return reason
    return None


# --------------------------------------------------------------------------- #
# Le controleur principal.
# --------------------------------------------------------------------------- #
@dataclass
class GuardResult:
    triage: dict[str, Any]         # le triage ajuste (copie)
    adjustments: list[str]         # journal des corrections, pour ticket_events
    alerts: list[str]              # ce qui doit remonter au canal d'administration

    @property
    def modified(self) -> bool:
        return bool(self.adjustments)


def _downgrade(triage: dict, adjustments: list[str], reason: str) -> None:
    if triage.get("autonomy_level") != "N3":
        adjustments.append(f"N3 force : {reason} (etait {triage.get('autonomy_level')})")
    triage["autonomy_level"] = "N3"
    if not triage.get("escalation_reason"):
        triage["escalation_reason"] = reason


def apply_guardrails(raw: dict[str, Any], ctx: TriageContext) -> GuardResult:
    """
    Prend le JSON de triage brut du modele et le contexte connu du code,
    renvoie un triage sur, avec le journal des corrections.
    """
    triage = dict(raw)                      # ne jamais muter l'entree
    adjustments: list[str] = []
    alerts: list[str] = []

    # 0. Le modele a signale quelque chose (injection, demande suspecte).
    if triage.get("alert"):
        alerts.append(str(triage["alert"]))

    # 1. Pas un ticket : rien a plafonner, on laisse passer tel quel.
    if triage.get("is_ticket") is False:
        return GuardResult(triage, adjustments, alerts)

    level = triage.get("autonomy_level")
    if level not in _ORDER:
        _downgrade(triage, adjustments, f"niveau invalide '{level}'")
        alerts.append(f"niveau d'autonomie invalide renvoye par le modele : {level!r}")
        level = "N3"

    # 2. Interdits permanents sur le titre + resume. Priorite absolue.
    forbidden = check_forbidden(f"{triage.get('title','')} {triage.get('summary','')}")
    if forbidden:
        _downgrade(triage, adjustments, f"interdit permanent : {forbidden}")
        alerts.append(f"interdit permanent detecte : {forbidden}")

    # 3. Confiance sous le seuil.
    conf = triage.get("confidence")
    if isinstance(conf, (int, float)) and conf < ctx.confidence_floor:
        _downgrade(triage, adjustments, f"confiance {conf} < {ctx.confidence_floor}")

    # 4. Categorie hors taxonomie.
    if ctx.taxonomy_categories and triage.get("category") not in ctx.taxonomy_categories:
        bad = triage.get("category")
        triage["category"] = "information"
        triage["subcategory"] = "question_procedure"
        adjustments.append(f"categorie hors taxonomie '{bad}' -> information")
        alerts.append(f"categorie inconnue proposee : {bad!r}")

    # 5. Plafond de maturite de la societe.
    allowed = _ALLOWED_BY_CAP.get(ctx.max_autonomy, {"N3"})
    if triage.get("autonomy_level") not in allowed:
        _downgrade(triage, adjustments,
                   f"plafond societe {ctx.max_autonomy} (proposait {triage.get('autonomy_level')})")

    # 6. Demandeur non verifie : pas d'action (N1) possible.
    if not ctx.identity.verified and triage.get("autonomy_level") == "N1":
        _downgrade(triage, adjustments, "demandeur non verifie, N1 interdit")

    # 7. Verifications specifiques a un N1 (execution d'un runbook).
    if triage.get("autonomy_level") == "N1":
        _verify_n1(triage, ctx, adjustments, alerts)

    # 8. Palier P0 : on garde le ticket, mais rien n'est publie.
    if ctx.observation_only:
        triage["_publish"] = False
        adjustments.append("palier P0 : ticket cree, aucune publication")
    else:
        triage.setdefault("_publish", True)

    return GuardResult(triage, adjustments, alerts)


def _verify_n1(triage: dict, ctx: TriageContext, adjustments: list[str], alerts: list[str]) -> None:
    code = triage.get("runbook_code")
    rb = ctx.runbooks.get(code) if code else None

    # a. Le runbook doit exister dans la liste fournie a l'agent.
    if rb is None:
        _downgrade(triage, adjustments, f"runbook '{code}' hors des runbooks disponibles")
        alerts.append(f"runbook inconnu propose par le modele : {code!r}")
        return

    # b. Le niveau du runbook doit etre coherent.
    if rb.autonomy_level != "N1":
        _downgrade(triage, adjustments,
                   f"runbook '{code}' n'est pas un N1 ({rb.autonomy_level})")
        return

    # c. Le role du demandeur doit etre autorise.
    if ctx.identity.role not in rb.allowed_roles:
        _downgrade(triage, adjustments,
                   f"role '{ctx.identity.role}' hors allowed_roles de '{code}'")
        return

    # d. Action pour un tiers : reservee a manager / it / admin.
    beneficiaire = triage.get("beneficiaire_email") or (triage.get("runbook_params") or {}).get("beneficiaire_email")
    demandeur = ctx.identity.email
    if beneficiaire and demandeur and beneficiaire.lower() != demandeur.lower():
        if ctx.identity.role not in ("manager", "it", "admin"):
            _downgrade(triage, adjustments,
                       "action pour un tiers sans role manager/it/admin")
            return

    # e. Le runbook exige la validation d'un manager : on l'annote pour WF-04.
    if rb.manager_approval:
        triage["_manager_approval_required"] = True
