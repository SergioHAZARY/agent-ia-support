# -*- coding: utf-8 -*-
"""
Micro-service Active Directory — le seul composant qui parle au contrôleur de
domaine. n8n l'appelle par une petite API ; lui seul détient le compte de
service, et il n'expose que les actions autorisées.

Conception (§10 et §12 du document) :
  - Il tourne sur le réseau interne, jamais exposé à Internet.
  - Il porte un compte de service à DROITS DÉLÉGUÉS MINIMAUX, jamais un
    administrateur de domaine.
  - Il rejette en CODE ce qui touche une OU non autorisée ou un groupe à
    privilèges — la délégation AD est une seconde barrière, pas la seule.
  - Il ne renvoie jamais un secret dans un canal collectif : le mot de passe
    temporaire d'une réinitialisation ne transite que par cette API
    serveur-à-serveur, et WF-05 le remet en message privé, jamais dans le groupe.

Lancer :  uvicorn main:app --host 10.0.0.X --port 8080
(voir README.md — on écoute sur l'IP WireGuard, pas sur 0.0.0.0)
"""

import os
import re
import ssl
import secrets
import logging
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field
from ldap3 import Server, Connection, Tls, ALL, MODIFY_REPLACE, MODIFY_ADD
from ldap3.core.exceptions import LDAPException

# --------------------------------------------------------------------------- #
# Configuration — tout vient de l'environnement, aucun secret en dur.
# --------------------------------------------------------------------------- #
AD_HOST        = os.environ["AD_HOST"]              # ex. 192.168.25.230
AD_DOMAIN      = os.environ["AD_DOMAIN"]            # ex. FSAGET.PRI
AD_BASE_DN     = os.environ["AD_BASE_DN"]           # ex. DC=FSAGET,DC=PRI
AD_BIND_USER   = os.environ["AD_BIND_USER"]         # compte de service, ex. svc-agent@FSAGET.PRI
AD_BIND_PASS   = os.environ["AD_BIND_PASS"]
API_TOKEN      = os.environ["API_TOKEN"]            # jeton partagé avec n8n

# OU dans lesquelles le service a le droit d'écrire. Une demande hors de cette
# liste est refusée AVANT tout appel au DC.
ALLOWED_OUS = [ou.strip() for ou in os.environ.get("ALLOWED_OUS", "").split(";") if ou.strip()]

# Groupes que le service ne touchera JAMAIS, quelle que soit la demande.
PRIVILEGED_GROUPS = {g.strip().lower() for g in os.environ.get(
    "PRIVILEGED_GROUPS",
    "domain admins;enterprise admins;schema admins;administrators;account operators;"
    "backup operators;server operators;print operators;group policy creator owners;"
    "administrateurs;admins du domaine;administrateurs de l'entreprise"
).split(";") if g.strip()}

# Vérification TLS du DC. Mettre à false SEULEMENT le temps d'un test, jamais en prod.
AD_TLS_VERIFY = os.environ.get("AD_TLS_VERIFY", "true").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("microservice-ad.log", encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger("ad")

app = FastAPI(title="Micro-service AD", version="1.0", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- #
# Connexion LDAPS
# --------------------------------------------------------------------------- #
def _connect() -> Connection:
    """Ouvre une connexion LDAPS au DC avec le compte de service."""
    validate = ssl.CERT_REQUIRED if AD_TLS_VERIFY else ssl.CERT_NONE
    tls = Tls(validate=validate, version=ssl.PROTOCOL_TLS_CLIENT)
    server = Server(AD_HOST, port=636, use_ssl=True, tls=tls, get_info=ALL)
    try:
        conn = Connection(server, user=AD_BIND_USER, password=AD_BIND_PASS,
                          auto_bind=True, raise_exceptions=True)
        return conn
    except LDAPException as e:
        log.error("bind échoué: %s", e)
        raise HTTPException(502, "connexion au contrôleur de domaine impossible")


def _require_token(authorization: str | None):
    """Le jeton partagé, comparé en temps constant."""
    expected = f"Bearer {API_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "jeton invalide")


def _ou_autorisee(ou_dn: str) -> bool:
    """L'OU cible doit figurer, exactement, dans la liste blanche."""
    ou_dn = ou_dn.strip().lower()
    return any(ou_dn == a.strip().lower() for a in ALLOWED_OUS)


def _find_user_dn(conn: Connection, upn_ou_email: str) -> str | None:
    """Retrouve le DN d'un utilisateur par UPN ou mail. None si absent."""
    flt = f"(&(objectClass=user)(|(userPrincipalName={upn_ou_email})(mail={upn_ou_email})))"
    conn.search(AD_BASE_DN, flt, attributes=["distinguishedName"])
    if conn.entries:
        return conn.entries[0].entry_dn
    return None


def _dn_est_sous_ou_autorisee(dn: str) -> bool:
    """Un utilisateur ne peut être modifié que s'il est dans une OU autorisée."""
    dn_l = dn.lower()
    return any(dn_l.endswith(a.strip().lower()) for a in ALLOWED_OUS)


SAM_RE = re.compile(r"[^a-z0-9._-]")


def _sam_account(prenom: str, nom: str) -> str:
    """prenom.nom, tronqué à 20 caractères (limite sAMAccountName)."""
    base = f"{prenom}.{nom}".lower().strip()
    base = SAM_RE.sub("", base.replace(" ", "."))
    return base[:20]


# --------------------------------------------------------------------------- #
# Modèles de requête — miroir des params_schema des runbooks
# --------------------------------------------------------------------------- #
class CreateUser(BaseModel):
    prenom: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    service: str
    manager_email: EmailStr
    date_arrivee: str
    intitule_poste: str | None = None
    ou_dn: str = Field(description="OU cible, doit figurer dans ALLOWED_OUS")


class ResetPassword(BaseModel):
    user_email: EmailStr
    verification_methode: str
    verifie_par: str


class DisableUser(BaseModel):
    user_email: EmailStr
    date_depart: str


class GroupChange(BaseModel):
    user_email: EmailStr
    groupe: str
    operation: str = Field(pattern="^(ajouter)$")  # retrait volontairement non exposé


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    """Vérifie que le DC répond, sans rien modifier. Appelé par la supervision."""
    conn = _connect()
    conn.unbind()
    return {"ok": True, "domaine": AD_DOMAIN, "ou_autorisees": len(ALLOWED_OUS)}


@app.post("/users")
def create_user(body: CreateUser, authorization: str = Header(None)):
    _require_token(authorization)

    if not _ou_autorisee(body.ou_dn):
        log.warning("REFUS création: OU non autorisée %s", body.ou_dn)
        raise HTTPException(403, "OU non autorisée pour ce service")

    conn = _connect()
    try:
        sam = _sam_account(body.prenom, body.nom)
        upn = f"{sam}@{AD_DOMAIN}"
        if _find_user_dn(conn, upn):
            raise HTTPException(409, f"un compte {upn} existe déjà")

        cn = f"{body.prenom} {body.nom}"
        user_dn = f"CN={cn},{body.ou_dn}"
        temp_pwd = _generate_password()

        attrs = {
            "givenName": body.prenom,
            "sn": body.nom,
            "displayName": cn,
            "userPrincipalName": upn,
            "sAMAccountName": sam,
            "mail": upn,
            "department": body.service,
            "manager": _find_user_dn(conn, body.manager_email),
        }
        if body.intitule_poste:
            attrs["title"] = body.intitule_poste

        if not conn.add(user_dn, ["top", "person", "organizationalPerson", "user"], attrs):
            raise HTTPException(502, f"création refusée par l'AD: {conn.result['description']}")

        # mot de passe (LDAPS obligatoire), puis activation + changement forcé
        conn.extend.microsoft.modify_password(user_dn, temp_pwd)
        conn.modify(user_dn, {
            "userAccountControl": [(MODIFY_REPLACE, [512])],   # NORMAL_ACCOUNT, activé
            "pwdLastSet": [(MODIFY_REPLACE, [0])],             # doit changer à la 1re connexion
        })

        log.info("CREATE %s par le service (OU=%s)", upn, body.ou_dn)
        # Le mot de passe temporaire ne part QUE dans cette réponse serveur-à-serveur.
        # WF-05 le remet en message PRIVÉ, jamais dans le groupe.
        return {"ok": True, "upn": upn, "sam": sam,
                "temp_password": temp_pwd, "must_change": True,
                "_avertissement": "temp_password = secret, remise en privé uniquement"}
    finally:
        conn.unbind()


@app.post("/users/reset-password")
def reset_password(body: ResetPassword, authorization: str = Header(None)):
    _require_token(authorization)

    conn = _connect()
    try:
        dn = _find_user_dn(conn, body.user_email)
        if not dn:
            raise HTTPException(404, "utilisateur introuvable")
        if not _dn_est_sous_ou_autorisee(dn):
            log.warning("REFUS reset: %s hors OU autorisée", body.user_email)
            raise HTTPException(403, "utilisateur hors du périmètre délégué")

        temp_pwd = _generate_password()
        conn.extend.microsoft.modify_password(dn, temp_pwd)
        conn.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, [0])]})

        log.info("RESET %s (vérifié par %s via %s)",
                 body.user_email, body.verifie_par, body.verification_methode)
        return {"ok": True, "user": body.user_email,
                "temp_password": temp_pwd, "must_change": True,
                "_avertissement": "temp_password = secret, remise en privé uniquement"}
    finally:
        conn.unbind()


@app.post("/users/disable")
def disable_user(body: DisableUser, authorization: str = Header(None)):
    _require_token(authorization)

    conn = _connect()
    try:
        dn = _find_user_dn(conn, body.user_email)
        if not dn:
            raise HTTPException(404, "utilisateur introuvable")
        if not _dn_est_sous_ou_autorisee(dn):
            raise HTTPException(403, "utilisateur hors du périmètre délégué")

        # Désactivation, jamais suppression (interdit permanent).
        conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [514])]})  # ACCOUNTDISABLE
        log.info("DISABLE %s (départ %s)", body.user_email, body.date_depart)
        return {"ok": True, "user": body.user_email, "action": "désactivé"}
    finally:
        conn.unbind()


@app.post("/users/groups")
def add_to_group(body: GroupChange, authorization: str = Header(None)):
    _require_token(authorization)

    if body.groupe.strip().lower() in PRIVILEGED_GROUPS:
        log.warning("REFUS groupe à privilèges: %s", body.groupe)
        raise HTTPException(403, "groupe à privilèges — interdit")

    conn = _connect()
    try:
        user_dn = _find_user_dn(conn, body.user_email)
        if not user_dn:
            raise HTTPException(404, "utilisateur introuvable")
        if not _dn_est_sous_ou_autorisee(user_dn):
            raise HTTPException(403, "utilisateur hors du périmètre délégué")

        conn.search(AD_BASE_DN, f"(&(objectClass=group)(cn={body.groupe}))",
                    attributes=["distinguishedName"])
        if not conn.entries:
            raise HTTPException(404, "groupe introuvable")
        group_dn = conn.entries[0].entry_dn

        conn.modify(group_dn, {"member": [(MODIFY_ADD, [user_dn])]})
        log.info("GROUP+ %s -> %s", body.user_email, body.groupe)
        return {"ok": True, "user": body.user_email, "groupe": body.groupe}
    finally:
        conn.unbind()


def _generate_password(length: int = 16) -> str:
    """Mot de passe temporaire respectant la complexité AD (4 classes)."""
    import string
    pools = [string.ascii_uppercase, string.ascii_lowercase,
             string.digits, "!@#$%*-_=+"]
    pwd = [secrets.choice(p) for p in pools]
    all_chars = "".join(pools)
    pwd += [secrets.choice(all_chars) for _ in range(length - len(pwd))]
    secrets.SystemRandom().shuffle(pwd)
    return "".join(pwd)
