# -*- coding: utf-8 -*-
"""
Polling Jira Bazarchic → Supabase (table events).

Récupère les tickets Jira créés/mis à jour depuis le dernier poll,
les normalise, et les insère dans la table events de Supabase.
Le pipeline n8n traitera ces events via le Schedule Trigger.

Ce script tourne en local (ou sur un serveur autorisé par la politique
IP Atlassian). n8n cloud est bloqué par les restrictions IP Jira.

Usage :
  python scripts/poll_jira.py              # un seul poll
  python scripts/poll_jira.py --loop 300   # boucle toutes les 300s (5 min)
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
import base64

# --- Configuration (variables d'env ou valeurs par défaut) ---
JIRA_SITE = os.environ.get("JIRA_SITE", "bzcmtc.atlassian.net")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "h.sergio.ext@bazarchic.com")
JIRA_TOKEN = os.environ.get("JIRA_TOKEN", "")

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

# JQL : tickets créés ou mis à jour dans les 10 dernières minutes
JQL = os.environ.get("JIRA_JQL", "updated >= -10m ORDER BY updated DESC")
MAX_RESULTS = 50


def jira_get(path, params=None):
    """GET sur l'API Jira avec Basic Auth."""
    url = f"https://{JIRA_SITE}{path}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(url)
    cred = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {cred}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def extract_description(desc):
    """Extrait le texte brut d'un champ description (ADF ou string)."""
    if not desc:
        return ""
    if isinstance(desc, str):
        return desc
    # Atlassian Document Format
    if isinstance(desc, dict) and "content" in desc:
        parts = []
        for block in desc.get("content", []):
            for inline in block.get("content", []):
                if inline.get("text"):
                    parts.append(inline["text"])
        return " ".join(parts)
    return str(desc)


def normalize_issue(issue):
    """Transforme un issue Jira en event normalisé."""
    f = issue.get("fields", {})
    rep = f.get("reporter") or {}
    self_url = issue.get("self", "")
    site_match = self_url.split("/rest/")[0].replace("https://", "").replace("http://", "") if self_url else JIRA_SITE

    return {
        "platform": "jira",
        "external_id": site_match,
        "source_message_id": issue.get("key", str(issue.get("id", ""))),
        "sender_raw": rep.get("displayName", rep.get("name", "")),
        "body": (f.get("summary", "") + " — " + extract_description(f.get("description"))).strip(" —"),
    }


def insert_events(events):
    """Insère les events dans Supabase via pg8000."""
    try:
        import pg8000
    except ImportError:
        print("ERREUR: pip install pg8000", file=sys.stderr)
        sys.exit(1)

    conn = pg8000.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DB,
        user=PG_USER, password=PG_PASS, ssl_context=True
    )
    conn.autocommit = True

    inserted = 0
    for ev in events:
        try:
            result = conn.run(
                "WITH ins AS ("
                "  INSERT INTO events (channel_id, tenant_id, source_message_id, sender_raw, body, received_at)"
                "  SELECT c.id, c.tenant_id, :msg_id, :sender, :body, now()"
                "  FROM channels c WHERE c.platform = :plat AND c.external_id = :ext_id AND c.status = 'active'"
                "  ON CONFLICT (channel_id, source_message_id) DO NOTHING"
                "  RETURNING id"
                ") SELECT (SELECT id FROM ins) AS event_id",
                msg_id=ev["source_message_id"],
                sender=ev["sender_raw"],
                body=ev["body"],
                plat=ev["platform"],
                ext_id=ev["external_id"],
            )
            if result and result[0][0] is not None:
                inserted += 1
                print(f"  + {ev['source_message_id']:15s} {ev['body'][:60]}")
        except Exception as e:
            print(f"  ! {ev['source_message_id']}: {e}", file=sys.stderr)

    conn.close()
    return inserted


def poll_once():
    """Un cycle de polling."""
    print(f"[{time.strftime('%H:%M:%S')}] Polling Jira ({JIRA_SITE})...")
    try:
        data = jira_get("/rest/api/3/search/jql", {
            "jql": JQL,
            "maxResults": MAX_RESULTS,
            "fields": "summary,description,reporter,project,issuetype,priority,status,created,updated"
        })
    except urllib.error.HTTPError as e:
        print(f"  Erreur Jira: {e.code} {e.read().decode()[:200]}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"  Erreur: {e}", file=sys.stderr)
        return 0

    issues = data.get("issues", [])
    print(f"  {len(issues)} tickets trouvés (JQL: {JQL})")

    if not issues:
        return 0

    events = [normalize_issue(iss) for iss in issues]
    inserted = insert_events(events)
    print(f"  {inserted} nouveaux events insérés")
    return inserted


def main():
    if not JIRA_TOKEN:
        print("JIRA_TOKEN manquant. Définir la variable d'environnement.", file=sys.stderr)
        print("  set JIRA_TOKEN=ATATT3x...", file=sys.stderr)
        sys.exit(1)
    if not PG_PASS:
        print("PG_PASS manquant. Définir la variable d'environnement.", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300
        print(f"Mode boucle : poll toutes les {interval}s. Ctrl+C pour arrêter.")
        while True:
            poll_once()
            time.sleep(interval)
    else:
        poll_once()


if __name__ == "__main__":
    main()
