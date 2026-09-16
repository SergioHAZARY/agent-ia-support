# -*- coding: utf-8 -*-
"""
Deploie un ou plusieurs fichiers workflow JSON vers n8n, de facon idempotente
(upsert par nom : met a jour si le nom existe, cree sinon).

Ne touche JAMAIS un workflow dont le nom ne commence pas par le prefixe
attendu -- garde-fou contre toute modification des workflows d'autres projets
sur cette instance partagee.

Config par variables d'environnement :
  N8N_BASE_URL   ex. https://dev-ai.app.n8n.cloud
  N8N_API_KEY    la cle API n8n
  N8N_PREFIX     prefixe protege (defaut : "AgentSupport -")

Usage :
  python n8n/sync.py                      # deploie tous les n8n/workflows/*.json
  python n8n/sync.py path/to/one.json     # deploie un fichier precis
"""
import glob
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

BASE = os.environ.get("N8N_BASE_URL", "").rstrip("/") + "/api/v1"
KEY = os.environ.get("N8N_API_KEY", "")
PREFIX = os.environ.get("N8N_PREFIX", "AgentSupport -")
HERE = os.path.dirname(os.path.abspath(__file__))


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


def existing_by_name():
    m = {}
    st, body = call("GET", "/workflows", params={"limit": 250})
    if isinstance(body, dict):
        for w in body.get("data", []):
            m[w.get("name")] = w.get("id")
    return m


def clean(wf):
    """Ne garder que les champs acceptes par POST/PUT /workflows."""
    return {k: wf[k] for k in ("name", "nodes", "connections", "settings") if k in wf}


def activate(wf_id):
    """Desactive puis reactive pour forcer le remontage des webhooks sur n8n cloud."""
    call("PATCH", f"/workflows/{wf_id}", body={"active": False})
    import time; time.sleep(2)
    st, body = call("PATCH", f"/workflows/{wf_id}", body={"active": True})
    activated = isinstance(body, dict) and body.get("active") is True
    print(f"  {'ACTIF' if activated else 'INACTIF':6} (toggle off/on pour webhooks)")
    return activated


def upsert(path, existing):
    wf = json.load(open(path, encoding="utf-8"))
    name = wf.get("name", "")
    if not name.startswith(PREFIX):
        print(f"  IGNORE {os.path.basename(path)} : nom '{name}' hors prefixe '{PREFIX}'")
        return False
    payload = clean(wf)
    if name in existing:
        wf_id = existing[name]
        st, body = call("PUT", f"/workflows/{wf_id}", body=payload)
        action = "MAJ "
    else:
        st, body = call("POST", "/workflows", body=payload)
        wf_id = body.get("id") if isinstance(body, dict) else None
        action = "CREE"
    ok = isinstance(body, dict) and body.get("id")
    print(f"  {action} {name:40} -> {st} {'id='+body['id'] if ok else body}")
    # Toujours reactiver apres MAJ pour remonter les webhooks
    if ok and wf_id:
        activate(wf_id)
    return bool(ok)


def main():
    if not BASE.startswith("http") or not KEY:
        print("N8N_BASE_URL ou N8N_API_KEY manquant.", file=sys.stderr)
        sys.exit(2)
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(HERE, "workflows", "*.json")))
    if not files:
        print("Aucun fichier workflow a deployer.")
        return
    existing = existing_by_name()
    ok = all(upsert(f, existing) for f in files)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
