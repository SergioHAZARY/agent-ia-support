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
    # Note : active est read-only dans PUT, la gestion se fait dans toggle_workflow

    st, body = call("GET", "/workflows", params={"limit": 250})
    all_workflows = []
    if isinstance(body, dict):
        all_workflows = body.get("data", [])
    elif isinstance(body, list):
        all_workflows = body

    # Diagnostic : lister tous les workflows
    print(f"\n  --- Workflows trouves ({len(all_workflows)}) ---")
    existing = {}
    duplicates = []
    for w in all_workflows:
        wname = w.get("name", "?")
        wid = w.get("id", "?")
        active = w.get("active", False)
        tags = [t.get("name", "") for t in w.get("tags", [])]
        project = w.get("homeProject", {}).get("name", "?") if w.get("homeProject") else "?"
        print(f"    {wname} | id={wid} | active={active} | project={project}")
        if wname == name:
            duplicates.append(w)
        if wname.startswith(PREFIX):
            existing[wname] = wid

    if len(duplicates) > 1:
        print(f"\n  ⚠ DOUBLON : {len(duplicates)} workflows nommes '{name}'")
        # Garder le plus recent, supprimer les autres
        duplicates.sort(key=lambda w: w.get("updatedAt", ""), reverse=True)
        for dup in duplicates[1:]:
            print(f"    Suppression doublon id={dup['id']} (ancien)")
            call("DELETE", f"/workflows/{dup['id']}")
        existing[name] = duplicates[0]["id"]

    if name in existing:
        wf_id = existing[name]
        st, body = call("PUT", f"/workflows/{wf_id}", body=payload)
        print(f"\n  MAJ {name} (id={wf_id}) -> {st}")
        if st not in (200, 201):
            print(f"  Detail : {body}")
            # Le PUT a echoue mais le workflow existe — on continue avec l'ID existant
            return wf_id
    else:
        st, body = call("POST", "/workflows", body=payload)
        print(f"\n  CREE {name} -> {st}")
        wf_id = body.get("id") if isinstance(body, dict) else None

    if isinstance(body, dict) and body.get("id"):
        return body["id"]
    if wf_id:
        return wf_id
    print(f"  ERREUR : {body}")
    return None


# ---- 3. Toggle workflow (off/on) pour re-enregistrer les webhooks -----------
def toggle_workflow(wf_id):
    print("\n=== Activation workflow (toggle off/on pour webhooks) ===")
    import time
    # Le PUT a deja mis active:false. On s'assure que l'etat est bien inactif.
    st1, r1 = call("POST", f"/workflows/{wf_id}/deactivate")
    if st1 not in (200, 201):
        st1, r1 = call("PATCH", f"/workflows/{wf_id}", body={"active": False})
    print(f"  Desactive -> {st1}")
    time.sleep(3)
    # Activer via POST (methode principale sur n8n cloud)
    st2, r2 = call("POST", f"/workflows/{wf_id}/activate")
    print(f"  POST activate -> {st2}")
    # Verifier le statut reel
    st3, r3 = call("GET", f"/workflows/{wf_id}")
    real_active = r3.get("active", "?") if isinstance(r3, dict) else "?"
    print(f"  Statut reel apres toggle : active={real_active}")
    if not real_active:
        # Fallback: PATCH
        st4, _ = call("PATCH", f"/workflows/{wf_id}", body={"active": True})
        print(f"  Fallback PATCH active:true -> {st4}")
        time.sleep(2)
        st5, r5 = call("GET", f"/workflows/{wf_id}")
        real2 = r5.get("active", "?") if isinstance(r5, dict) else "?"
        print(f"  Statut reel apres fallback : active={real2}")
    if real_active:
        print("  OK : workflow actif")
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


def update_credentials_json(updates):
    """Met a jour credentials.json avec les references de credentials creees."""
    creds_path = os.path.join(HERE, "credentials.json")
    if not os.path.exists(creds_path):
        return
    data = json.load(open(creds_path, encoding="utf-8"))
    changed = False
    for json_key, cred_info in updates.items():
        if cred_info and (json_key not in data or data[json_key].get("id") != cred_info["id"]):
            data[json_key] = cred_info
            changed = True
            print(f"  credentials.json: {json_key} -> id={cred_info['id']}")
    if changed:
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")


# --- Credentials Outlook OAuth2 pour les boites email ---
# Les credentials Microsoft Outlook OAuth2 se creent DANS l'interface n8n
# (flux interactif avec fenetre de connexion Microsoft).
# Cette fonction detecte celles qui existent deja et met a jour credentials.json.
OUTLOOK_ACCOUNTS = [
    {"json_key": "outlookBazarchic",    "name": "Outlook Bazarchic"},
    {"json_key": "outlookBeautyBay",    "name": "Outlook BeautyBay"},
    {"json_key": "outlookAtlasForMen",  "name": "Outlook AtlasForMen"},
    {"json_key": "outlookFrancoisSaget","name": "Outlook FrancoisSaget"},
    {"json_key": "outlookRegardBeauty", "name": "Outlook RegardBeauty"},
]


def discover_outlook_credentials():
    """Detecte les credentials Outlook OAuth2 deja creees dans n8n."""
    print("\n=== Credentials Outlook OAuth2 ===")
    st, body = call("GET", "/credentials", params={"limit": 200})
    existing = {}
    creds_list = body.get("data", body) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    for c in creds_list:
        existing[c.get("name")] = c

    results = {}
    missing = []
    for acct in OUTLOOK_ACCOUNTS:
        cred_name = acct["name"]
        if cred_name in existing:
            print(f"  OK  {cred_name} (id={existing[cred_name]['id']})")
            results[acct["json_key"]] = {"id": existing[cred_name]["id"], "name": cred_name}
        else:
            print(f"  MANQUE {cred_name} -> a creer dans n8n : Credentials > Microsoft Outlook OAuth2 API")
            missing.append(cred_name)

    if missing:
        print(f"\n  ⚠ {len(missing)} credential(s) Outlook a creer manuellement dans n8n.")
        print("  Procedure : n8n > Settings > Credentials > Add Credential")
        print("    > Microsoft Outlook OAuth2 API > Connect > se connecter avec le compte")
        print("    > Renommer la credential exactement comme indique ci-dessus.")
    return results


def ensure_jira_beautybay_credential():
    """Cree la credential HTTP Basic pour Jira BeautyBay si absente."""
    print("\n=== Credential Jira BeautyBay ===")
    user = os.environ.get("JIRA_BEAUTYBAY_USER", "")
    token = os.environ.get("JIRA_BEAUTYBAY_TOKEN", "")
    if not user or not token:
        print("  SKIP (pas de JIRA_BEAUTYBAY_USER/TOKEN)")
        return None

    cred_name = "Jira BeautyBay (HTTP Basic)"
    st, body = call("GET", "/credentials", params={"limit": 200})
    creds_list = body.get("data", body) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    for c in creds_list:
        if c.get("name") == cred_name:
            print(f"  OK  {cred_name} (deja presente, id={c['id']})")
            return {"id": c["id"], "name": cred_name}

    payload = {
        "name": cred_name,
        "type": "httpBasicAuth",
        "data": {"user": user, "password": token},
    }
    st2, r = call("POST", "/credentials", body=payload)
    if st2 in (200, 201) and isinstance(r, dict) and r.get("id"):
        print(f"  CREE {cred_name} -> id={r['id']}")
        return {"id": r["id"], "name": cred_name}
    print(f"  ERREUR : {st2} {str(r)[:200]}")
    return None


def main():
    if not BASE.startswith("http") or not KEY:
        print("N8N_BASE_URL ou N8N_API_KEY manquant.", file=sys.stderr)
        print("Definir les variables d'environnement ou lancer via CI.", file=sys.stderr)
        sys.exit(2)

    ensure_variables()

    # Credentials
    cred_updates = {}
    admin_cred = ensure_admin_credential()
    if admin_cred:
        cred_updates["telegramAdminApi"] = admin_cred

    outlook_creds = discover_outlook_credentials()
    cred_updates.update(outlook_creds)

    jira_bb = ensure_jira_beautybay_credential()
    if jira_bb:
        cred_updates["jiraHttpBeautyBay"] = jira_bb

    if cred_updates:
        update_credentials_json(cred_updates)

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
