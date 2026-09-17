# -*- coding: utf-8 -*-
"""
Polling Jira Bazarchic → Supabase (table events).

Récupère TOUS les tickets Jira (À Faire, En cours, Code Review, Bloqué,
A Tester, A Livrer en Prod, Terminé, etc.) avec pagination, les normalise,
et les insère dans Supabase. Le dédoublonnage est fait par ON CONFLICT.

Mode --init   : import initial de TOUS les tickets existants (peut être long)
Mode --loop N : poll incrémental toutes les N secondes (défaut 300 = 5 min)
Mode par défaut : un seul poll incrémental (tickets mis à jour < 10 min)

Ce script tourne en local car n8n cloud est bloqué par les restrictions IP Jira.

Usage :
  set JIRA_TOKEN=ATATT3x...
  set PG_PASS=Orbyone_419
  python scripts/poll_jira.py --init        # import initial complet
  python scripts/poll_jira.py --loop 300    # poll continu (5 min)
  python scripts/poll_jira.py               # un seul poll incrémental
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
import base64

# --- Configuration ---
JIRA_SITE = os.environ.get("JIRA_SITE", "bzcmtc.atlassian.net")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "h.sergio.ext@bazarchic.com")
JIRA_TOKEN = os.environ.get("JIRA_TOKEN", "")

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

PAGE_SIZE = 100


def jira_get(path, params=None):
    url = f"https://{JIRA_SITE}{path}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(url)
    cred = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {cred}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def extract_description(desc):
    if not desc:
        return ""
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict) and "content" in desc:
        parts = []
        for block in desc.get("content", []):
            for inline in block.get("content", []):
                if inline.get("text"):
                    parts.append(inline["text"])
        return " ".join(parts)
    return str(desc)


def normalize_issue(issue):
    f = issue.get("fields", {})
    rep = f.get("reporter") or {}
    self_url = issue.get("self", "")
    site = self_url.split("/rest/")[0].replace("https://", "").replace("http://", "") if self_url else JIRA_SITE
    status = (f.get("status") or {}).get("name", "")
    priority = (f.get("priority") or {}).get("name", "")
    project = (f.get("project") or {}).get("key", "")
    itype = (f.get("issuetype") or {}).get("name", "")

    summary = f.get("summary", "")
    desc = extract_description(f.get("description"))
    body = f"[{project}] [{status}] [{itype}] {summary}"
    if desc:
        body += f" — {desc}"

    return {
        "platform": "jira",
        "external_id": site,
        "source_message_id": issue.get("key", str(issue.get("id", ""))),
        "sender_raw": rep.get("displayName", rep.get("name", "")),
        "body": body[:2000],
    }


def get_pg_conn():
    import pg8000
    return pg8000.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DB,
        user=PG_USER, password=PG_PASS, ssl_context=True
    )


BATCH_SIZE = 200  # reconnexion toutes les N insertions (évite timeout Supabase)


def insert_events(events):
    inserted = 0
    conn = None
    for i, ev in enumerate(events):
        # Reconnexion par batch pour éviter les timeouts réseau
        if conn is None or i % BATCH_SIZE == 0:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = get_pg_conn()
            conn.autocommit = True
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
                print(f"  + {ev['source_message_id']:18s} {ev['body'][:70]}")
        except Exception as e:
            print(f"  ! {ev['source_message_id']}: {e}", file=sys.stderr)
            # Forcer la reconnexion au prochain tour
            try:
                conn.close()
            except Exception:
                pass
            conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    return inserted


def fetch_all_issues(jql):
    """Récupère tous les tickets avec pagination (nextPageToken pour /search/jql)."""
    all_issues = []
    next_token = None
    while True:
        params = {
            "jql": jql,
            "maxResults": PAGE_SIZE,
            "fields": "summary,description,reporter,project,issuetype,priority,status,created,updated"
        }
        if next_token:
            params["nextPageToken"] = next_token
        data = jira_get("/rest/api/3/search/jql", params)
        issues = data.get("issues", [])
        all_issues.extend(issues)
        print(f"  ... {len(all_issues)} tickets chargés")
        next_token = data.get("nextPageToken")
        is_last = data.get("isLast", True)
        if is_last or not issues or not next_token:
            break
    return all_issues


def poll_init():
    """Import initial : TOUS les tickets, tous statuts, tous projets."""
    print(f"[{time.strftime('%H:%M:%S')}] === IMPORT INITIAL Jira ({JIRA_SITE}) ===")
    print("  Récupération de TOUS les tickets (toutes les statuts)...")
    jql = "created IS NOT EMPTY ORDER BY created ASC"
    issues = fetch_all_issues(jql)
    print(f"  Total: {len(issues)} tickets Jira")
    events = [normalize_issue(iss) for iss in issues]
    inserted = insert_events(events)
    print(f"  === {inserted} nouveaux events insérés (sur {len(events)} tickets) ===")
    return inserted


def poll_incremental():
    """Poll incrémental : tickets mis à jour dans les 10 dernières minutes."""
    print(f"[{time.strftime('%H:%M:%S')}] Polling Jira ({JIRA_SITE})...")
    jql = "updated >= -10m ORDER BY updated DESC"
    try:
        issues = fetch_all_issues(jql)
    except urllib.error.HTTPError as e:
        print(f"  Erreur Jira: {e.code} {e.read().decode()[:200]}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"  Erreur: {e}", file=sys.stderr)
        return 0

    if not issues:
        print("  Aucun ticket mis à jour")
        return 0

    events = [normalize_issue(iss) for iss in issues]
    inserted = insert_events(events)
    print(f"  {inserted} nouveaux events insérés")
    return inserted


def main():
    if not JIRA_TOKEN:
        print("JIRA_TOKEN manquant. set JIRA_TOKEN=ATATT3x...", file=sys.stderr)
        sys.exit(1)
    if not PG_PASS:
        print("PG_PASS manquant. set PG_PASS=...", file=sys.stderr)
        sys.exit(1)

    try:
        import pg8000  # noqa
    except ImportError:
        print("ERREUR: pip install pg8000", file=sys.stderr)
        sys.exit(1)

    if "--init" in sys.argv:
        poll_init()
    elif "--loop" in sys.argv:
        idx = sys.argv.index("--loop")
        interval = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 300
        print(f"Mode boucle : poll toutes les {interval}s. Ctrl+C pour arrêter.")
        while True:
            poll_incremental()
            time.sleep(interval)
    else:
        poll_incremental()


if __name__ == "__main__":
    main()
