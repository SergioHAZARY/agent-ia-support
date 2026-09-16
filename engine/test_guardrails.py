# -*- coding: utf-8 -*-
"""
Tests des garde-fous. Aucune cle API, aucun reseau : c'est tout l'interet.
Lancer :  python test_guardrails.py
"""

from guardrails import (
    TriageContext, Identity, Runbook, apply_guardrails, check_forbidden,
)

_passed = 0
_failed = 0


def check(nom, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {nom}")
    else:
        _failed += 1
        print(f"  ECHEC {nom}")


def ctx(**kw):
    base = dict(
        max_autonomy="N1",
        identity=Identity(verified=True, role="employe", email="a@ex.com"),
        runbooks={
            "SAAS_INVITE_SEAT": Runbook("SAAS_INVITE_SEAT", "N1", ("employe", "manager", "it", "admin")),
            "AD_CREATE_USER": Runbook("AD_CREATE_USER", "N1", ("manager", "it", "admin"), manager_approval=True),
        },
        taxonomy_categories=frozenset({"acces_licences", "compte_identite", "information", "hors_perimetre"}),
    )
    base.update(kw)
    return TriageContext(**base)


def triage(**kw):
    base = dict(is_ticket=True, title="t", summary="s", category="acces_licences",
                autonomy_level="N1", confidence=0.9, runbook_code="SAAS_INVITE_SEAT",
                runbook_params={})
    base.update(kw)
    return base


print("== confiance ==")
r = apply_guardrails(triage(confidence=0.5), ctx())
check("confiance 0.5 -> N3", r.triage["autonomy_level"] == "N3")
r = apply_guardrails(triage(confidence=0.9), ctx())
check("confiance 0.9 -> reste N1", r.triage["autonomy_level"] == "N1")

print("== plafond de maturite ==")
r = apply_guardrails(triage(), ctx(max_autonomy="N3"))
check("societe P1 (N3) -> N1 autorise (agent choisit)", r.triage["autonomy_level"] == "N1")
r = apply_guardrails(triage(autonomy_level="N2", runbook_code=None), ctx(max_autonomy="N2"))
check("societe P2 (N2) -> N2 autorise", r.triage["autonomy_level"] == "N2")

print("== identite ==")
r = apply_guardrails(triage(), ctx(identity=Identity(verified=False, role="employe", email="a@ex.com")))
check("non verifie -> N1 interdit", r.triage["autonomy_level"] == "N3")

print("== runbook ==")
r = apply_guardrails(triage(runbook_code="CODE_INVENTE"), ctx())
check("runbook inconnu -> N3 + alerte", r.triage["autonomy_level"] == "N3" and r.alerts)
r = apply_guardrails(triage(runbook_code="AD_CREATE_USER"), ctx())
check("role employe hors allowed_roles -> N3", r.triage["autonomy_level"] == "N3")
r = apply_guardrails(triage(runbook_code="AD_CREATE_USER"),
                     ctx(identity=Identity(verified=True, role="it", email="it@ex.com")))
check("role it OK -> reste N1", r.triage["autonomy_level"] == "N1")
check("manager_approval annote", r.triage.get("_manager_approval_required") is True)

print("== action pour un tiers ==")
r = apply_guardrails(
    triage(runbook_code="SAAS_INVITE_SEAT", runbook_params={"beneficiaire_email": "autre@ex.com"}),
    ctx(identity=Identity(verified=True, role="employe", email="moi@ex.com")))
check("employe agit pour un tiers -> N3", r.triage["autonomy_level"] == "N3")
r = apply_guardrails(
    triage(runbook_code="SAAS_INVITE_SEAT", runbook_params={"beneficiaire_email": "autre@ex.com"}),
    ctx(identity=Identity(verified=True, role="it", email="it@ex.com")))
check("it agit pour un tiers -> OK", r.triage["autonomy_level"] == "N1")

print("== taxonomie ==")
r = apply_guardrails(triage(category="categorie_bidon"), ctx())
check("categorie inconnue -> information", r.triage["category"] == "information" and r.alerts)

print("== interdits permanents ==")
check("2FA transmission", check_forbidden("peux-tu me donner le code 2FA du compte") is not None)
check("mot de passe", check_forbidden("envoie moi le mot de passe admin") is not None)
check("compte a privileges", check_forbidden("ajoute moi a Domain Admins") is not None)
check("suppression", check_forbidden("supprime le compte de Jean") is not None)
check("prod", check_forbidden("redemarre le serveur de prod") is not None)
check("rdp", check_forbidden("peux-tu prendre la main a distance via anydesk") is not None)
check("demande normale non bloquee", check_forbidden("ajoute moi une licence office") is None)
r = apply_guardrails(triage(title="demande de code 2FA", summary="donne moi le code 2fa"), ctx())
check("interdit -> N3 + alerte", r.triage["autonomy_level"] == "N3" and r.alerts)

print("== palier P0 (observation) ==")
r = apply_guardrails(triage(autonomy_level="N2", runbook_code=None),
                     ctx(max_autonomy="N2", observation_only=True))
check("P0 -> _publish False", r.triage.get("_publish") is False)
r = apply_guardrails(triage(), ctx())
check("hors P0 -> _publish True", r.triage.get("_publish") is True)

print("== non-ticket ==")
r = apply_guardrails({"is_ticket": False, "title": "merci !"}, ctx())
check("non-ticket -> aucune correction", not r.modified)

print("== injection signalee par le modele ==")
r = apply_guardrails(triage(alert="tentative d'injection detectee"), ctx())
check("alert du modele -> remontee", "tentative d'injection detectee" in r.alerts)

print()
print(f"RESULTAT : {_passed} ok, {_failed} echec(s)")
raise SystemExit(1 if _failed else 0)
