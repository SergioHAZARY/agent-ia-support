# -*- coding: utf-8 -*-
"""
Deploiement complet : credentials + variables + build + workflow + activation.

Usage :
  python n8n/deploy_full.py

Prerequis (variables d'environnement) :
  N8N_BASE_URL, N8N_API_KEY  (comme sync.py)
  CLICKUP_API_TOKEN           (optionnel, pour creer la variable n8n)
  TELEGRAM_ADMIN_BOT_TOKEN    (optionnel, pour creer la credential admin)
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error

BASE = os.environ.get("N8N_BASE_URL", "").rstrip("/") + "/api/v1"
KEY = os.environ.get("N8N_API_KEY", "")
HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "AgentSupport -"


def call(method, path, body=None, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-N8N-API-KEY", KEY)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]


# ---- 1. Variables n8n --------------------------------------------------------
REQUIRED_VARS = {
    "CLICKUP_API_TOKEN": os.environ.get("CLICKUP_API_TOKEN", ""),
    "CLICKUP_LIST_ID": "901222267724",
    "TELEGRAM_DEVMG_CHAT_ID": "-5219441607",
    "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "TELEGRAM_ADMIN_BOT_TOKEN": os.environ.get("TELEGRAM_ADMIN_BOT_TOKEN", ""),
}


def ensure_variables():
    print("\n=== Variables n8n ===")
    st, body = call("GET", "/variables")
    existing = {}
    if isinstance(body, dict):
        for v in body.get("data", []):
            existing[v["key"]] = v
    elif isinstance(body, list):
        for v in body:
            existing[v["key"]] = v

    for key, default_val in REQUIRED_VARS.items():
        if key in existing:
            print(f"  OK  {key} (deja presente)")
        elif default_val:
            st2, r = call("POST", "/variables", body={"key": key, "value": default_val})
            print(f"  {'CREE' if st2 in (200,201) else 'ERREUR'} {key} -> {st2}")
        else:
            print(f"  SKIP {key} (pas de valeur fournie, a creer manuellement)")


# ---- 2. Deploy workflow ------------------------------------------------------
def deploy_workflow():
    print("\n=== Deploiement workflow ===")
    wf_path = os.path.join(HERE, "workflows", "agentsupport-pipeline.json")
    if not os.path.exists(wf_path):
        print("  ERREUR : fichier workflow introuvable, lancer build_pipeline.py d'abord")
        return None

    wf = json.load(open(wf_path, encoding="utf-8"))
    name = wf.get("name", "")
    if not name.startswith(PREFIX):
        print(f"  ERREUR : nom '{name}' hors prefixe '{PREFIX}'")
        return None

    payload = {k: wf[k] for k in ("name", "nodes", "connections", "settings") if k in wf}

    st, body = call("GET", "/workflows", params={"limit": 250})
    existing = {}
    if isinstance(body, dict):
        for w in body.get("data", []):
            existing[w.get("name")] = w.get("id")

    if name in existing:
        wf_id = existing[name]
        st, body = call("PUT", f"/workflows/{wf_id}", body=payload)
        print(f"  MAJ {name} -> {st}")
    else:
        st, body = call("POST", "/workflows", body=payload)
        print(f"  CREE {name} -> {st}")
        wf_id = body.get("id") if isinstance(body, dict) else None

    if isinstance(body, dict) and body.get("id"):
        return body["id"]
    print(f"  ERREUR : {body}")
    return None


# ---- 3. Toggle workflow (off/on) pour re-enregistrer les webhooks -----------
def toggle_workflow(wf_id):
    print("\n=== Activation workflow (toggle off/on pour webhooks) ===")
    # n8n cloud : POST /workflows/{id}/activate et /deactivate
    st1, _ = call("POST", f"/workflows/{wf_id}/deactivate")
    if st1 in (404, 405):
        # Fallback API v1 : PATCH avec body
        st1, _ = call("PATCH", f"/workflows/{wf_id}", body={"active": False})
    print(f"  Desactive -> {st1}")
    st2, _ = call("POST", f"/workflows/{wf_id}/activate")
    if st2 in (404, 405):
        st2, _ = call("PATCH", f"/workflows/{wf_id}", body={"active": True})
    print(f"  Active    -> {st2}")
    if st2 == 200:
        print("  OK : workflow actif, webhooks re-enregistres")
    else:
        print("  ATTENTION : activer manuellement dans l'editeur n8n")


def ensure_admin_credential():
    """Cree la credential Telegram pour le bot admin si absente."""
    print("\n=== Credential bot admin ===")
    token = os.environ.get("TELEGRAM_ADMIN_BOT_TOKEN", "")
    if not token:
        st, body = call("GET", "/variables")
        existing = {}
        if isinstance(body, dict):
            for v in body.get("data", []):
                existing[v["key"]] = v.get("value", "")
        elif isinstance(body, list):
            for v in body:
                existing[v["key"]] = v.get("value", "")
        token = existing.get("TELEGRAM_ADMIN_BOT_TOKEN", "")
    if not token:
        print("  SKIP (pas de TELEGRAM_ADMIN_BOT_TOKEN)")
        return None

    cred_name = "Telegram Admin Bot"
    st, body = call("GET", "/credentials", params={"limit": 200})
    creds = body.get("data", body) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    for c in creds:
        if c.get("name") == cred_name:
            print(f"  OK  {cred_name} (deja presente, id={c['id']})")
            return {"id": c["id"], "name": cred_name}

    payload = {
        "name": cred_name,
        "type": "telegramApi",
        "data": {"accessToken": token},
    }
    st2, r = call("POST", "/credentials", body=payload)
    if st2 in (200, 201) and isinstance(r, dict) and r.get("id"):
        print(f"  CREE {cred_name} -> id={r['id']}")
        return {"id": r["id"], "name": cred_name}
    else:
        print(f"  ERREUR creation credential : {st2} {str(r)[:200]}")
        return None


def update_credentials_json(admin_cred):
    """Met a jour credentials.json avec la reference du bot admin."""
    creds_path = os.path.join(HERE, "credentials.json")
    if not os.path.exists(creds_path):
        return
    data = json.load(open(creds_path, encoding="utf-8"))
    if "telegramAdminApi" in data and data["telegramAdminApi"].get("id") == admin_cred["id"]:
        return
    data["telegramAdminApi"] = admin_cred
    with open(creds_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  credentials.json mis a jour avec telegramAdminApi")


def main():
    if not BASE.startswith("http") or not KEY:
        print("N8N_BASE_URL ou N8N_API_KEY manquant.", file=sys.stderr)
        print("Definir les variables d'environnement ou lancer via CI.", file=sys.stderr)
        sys.exit(2)

    ensure_variables()
    admin_cred = ensure_admin_credential()
    if admin_cred:
        update_credentials_json(admin_cred)

    # Rebuild le pipeline (credentials.json potentiellement mis a jour)
    print("\n=== Build pipeline ===")
    build_script = os.path.join(HERE, "build_pipeline.py")
    rc = subprocess.call([sys.executable, build_script])
    if rc != 0:
        print("  ERREUR build_pipeline.py", file=sys.stderr)
        sys.exit(rc)

    wf_id = deploy_workflow()
    if wf_id:
        toggle_workflow(wf_id)
    print("\n=== Termine ===")


if __name__ == "__main__":
    main()
