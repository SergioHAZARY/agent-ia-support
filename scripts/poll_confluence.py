# -*- coding: utf-8 -*-
"""
Polling Confluence Bazarchic -> Supabase (table events).

Recupere toutes les pages et billets de blog Confluence avec pagination,
les normalise, et les insere dans Supabase. Le dedoublonnage est fait
par ON CONFLICT (channel_id, source_message_id).

Mode --init      : import initial de TOUTES les pages existantes
Mode --loop N    : poll incremental toutes les N secondes (defaut 300 = 5 min)
Mode par defaut  : un seul poll incremental (pages mises a jour < 10 min)

Usage :
  set CONFLUENCE_TOKEN=ATATT3x...
  set PG_PASS=Orbyone_419
  python scripts/poll_confluence.py --init
  python scripts/poll_confluence.py --loop 300
  python scripts/poll_confluence.py
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import base64

# --- Configuration ---
CONFLUENCE_SITE  = os.environ.get("CONFLUENCE_SITE",  "bzcmtc.atlassian.net")
CONFLUENCE_EMAIL = os.environ.get("CONFLUENCE_EMAIL", "h.sergio.ext@bazarchic.com")
CONFLUENCE_TOKEN = os.environ.get("CONFLUENCE_TOKEN", os.environ.get("JIRA_TOKEN", ""))

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB   = os.environ.get("PG_DB",   "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

PAGE_SIZE  = 100
BATCH_SIZE = 200


# ---------------------------------------------------------------------------
# Confluence API helpers
# ---------------------------------------------------------------------------

def _auth_header():
    cred = base64.b64encode(f"{CONFLUENCE_EMAIL}:{CONFLUENCE_TOKEN}".encode()).decode()
    return f"Basic {cred}"


def confluence_get(path, params=None):
    """GET against the Confluence REST API (wiki base)."""
    url = f"https://{CONFLUENCE_SITE}/wiki/rest/api{path}"
    if params:
        url += "?" + "&".join(
            f"{k}={urllib.request.quote(str(v))}" for k, v in params.items()
        )
    req = urllib.request.Request(url)
    req.add_header("Authorization", _auth_header())
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

def strip_html(html):
    """Remove HTML tags and collapse whitespace."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_page(page):
    """Normalize a Confluence page or blogpost into an event dict."""
    space      = page.get("space") or {}
    space_key  = space.get("key", "")
    title      = page.get("title", "")
    history    = page.get("history") or {}
    created_by = history.get("createdBy") or {}
    creator    = created_by.get("displayName", created_by.get("username", "unknown"))

    # Extract plain-text preview from body.storage if present
    body_obj   = page.get("body") or {}
    storage    = body_obj.get("storage") or {}
    raw_html   = storage.get("value", "")
    content    = strip_html(raw_html)

    prefix = f"[{space_key}/{title}]"
    if content:
        body_text = f"{prefix} {content}"
    else:
        body_text = prefix

    return {
        "platform":         "confluence",
        "external_id":      CONFLUENCE_SITE,
        "source_message_id": str(page.get("id", "")),
        "sender_raw":       creator,
        "body":             body_text[:2000],
    }


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def fetch_content_type(content_type, expand, cql_filter=None):
    """
    Fetch all pages or blogposts with cursor-based pagination.

    content_type : 'page' or 'blogpost'
    expand       : comma-separated list of expand fields
    cql_filter   : optional lastModified filter string (e.g. '>= "2024-01-01"')
    """
    all_items = []
    start = 0
    while True:
        params = {
            "type":   content_type,
            "limit":  PAGE_SIZE,
            "start":  start,
            "expand": expand,
        }
        if cql_filter:
            # Use CQL search endpoint when filtering by date
            pass  # handled separately in fetch_recent_content

        try:
            data = confluence_get("/content", params)
        except urllib.error.HTTPError as e:
            print(f"  ! HTTP {e.code} fetching {content_type} at start={start}", file=sys.stderr)
            break
        except Exception as e:
            print(f"  ! Error fetching {content_type} at start={start}: {e}", file=sys.stderr)
            break

        results = data.get("results", [])
        all_items.extend(results)
        size     = data.get("size", 0)
        links    = data.get("_links") or {}
        has_next = bool(links.get("next"))

        print(f"  ... {len(all_items)} {content_type}(s) loaded")

        if not results or size < PAGE_SIZE or not has_next:
            break
        start += PAGE_SIZE

    return all_items


def fetch_recent_content(content_type, expand, minutes=10):
    """
    Fetch pages/blogposts updated in the last N minutes using CQL search.
    Returns a list of content items (may lack body.storage unless expanded).
    """
    all_items = []
    start = 0
    # CQL: lastModified >= now("-10m") AND type = page
    cql = f'type="{content_type}" AND lastModified >= now("-{minutes}m") ORDER BY lastModified ASC'

    while True:
        params = {
            "cql":    cql,
            "limit":  PAGE_SIZE,
            "start":  start,
            "expand": expand,
        }
        try:
            data = confluence_get("/content/search", params)
        except urllib.error.HTTPError as e:
            print(f"  ! HTTP {e.code} on CQL search: {e.read().decode()[:200]}", file=sys.stderr)
            break
        except Exception as e:
            print(f"  ! Error on CQL search: {e}", file=sys.stderr)
            break

        results = data.get("results", [])
        all_items.extend(results)
        size     = data.get("size", 0)
        links    = data.get("_links") or {}
        has_next = bool(links.get("next"))

        if not results or size < PAGE_SIZE or not has_next:
            break
        start += PAGE_SIZE

    return all_items


# ---------------------------------------------------------------------------
# PostgreSQL helpers (same pattern as poll_jira.py / poll_clickup.py)
# ---------------------------------------------------------------------------

def get_pg_conn():
    import pg8000
    return pg8000.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DB,
        user=PG_USER, password=PG_PASS, ssl_context=True
    )


def insert_events(events):
    """Insert events into Supabase with batch reconnect."""
    inserted = 0
    conn = None
    for i, ev in enumerate(events):
        # Reconnect every BATCH_SIZE rows to avoid Supabase pooler timeouts
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


# ---------------------------------------------------------------------------
# Poll modes
# ---------------------------------------------------------------------------

EXPAND_FULL   = "space,history,body.storage"
EXPAND_LIGHT  = "space,history"


def poll_init():
    """Full import: all pages + all blogposts."""
    print(f"[{time.strftime('%H:%M:%S')}] === IMPORT INITIAL Confluence ({CONFLUENCE_SITE}) ===")

    all_events = []

    print("  Fetching all pages...")
    pages = fetch_content_type("page", EXPAND_FULL)
    print(f"  {len(pages)} page(s) retrieved")

    print("  Fetching all blogposts...")
    blogs = fetch_content_type("blogpost", EXPAND_LIGHT)
    print(f"  {len(blogs)} blogpost(s) retrieved")

    for item in pages + blogs:
        all_events.append(normalize_page(item))

    print(f"  Total: {len(all_events)} item(s) to insert")
    inserted = insert_events(all_events)
    print(f"  === {inserted} new events inserted (out of {len(all_events)} items) ===")
    return inserted


def poll_incremental(minutes=10):
    """Incremental poll: pages/blogposts updated in the last N minutes."""
    print(f"[{time.strftime('%H:%M:%S')}] Polling Confluence ({CONFLUENCE_SITE}) last {minutes} min...")

    all_events = []

    pages = fetch_recent_content("page",     EXPAND_FULL,  minutes)
    blogs = fetch_recent_content("blogpost", EXPAND_LIGHT, minutes)

    if not pages and not blogs:
        print("  No recently updated content found")
        return 0

    print(f"  {len(pages)} page(s), {len(blogs)} blogpost(s) updated recently")

    for item in pages + blogs:
        all_events.append(normalize_page(item))

    inserted = insert_events(all_events)
    print(f"  {inserted} new events inserted")
    return inserted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not CONFLUENCE_TOKEN:
        print("CONFLUENCE_TOKEN manquant.", file=sys.stderr)
        print("  set CONFLUENCE_TOKEN=ATATT3x...", file=sys.stderr)
        print("  (ou set JIRA_TOKEN=... si vous utilisez le meme token)", file=sys.stderr)
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
        idx      = sys.argv.index("--loop")
        interval = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 300
        print(f"Loop mode: poll every {interval}s. Ctrl+C to stop.")
        while True:
            try:
                poll_incremental()
            except KeyboardInterrupt:
                print("Stopped.")
                break
            except Exception as e:
                print(f"  ! Unhandled error: {e}", file=sys.stderr)
            time.sleep(interval)
    else:
        poll_incremental()


if __name__ == "__main__":
    main()
