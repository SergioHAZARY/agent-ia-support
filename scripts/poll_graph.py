# -*- coding: utf-8 -*-
"""
Polling Microsoft 365 via Graph API -> Supabase (table events).

Utilise le flux OAuth2 Client Credentials (application permissions)
pour contourner MFA et Basic Auth desactive. Fonctionne pour :
  - Emails Outlook (permission Mail.Read)
  - Messages Teams (permission ChannelMessage.Read.All)

Prerequis :
  1. Creer une App Registration dans Azure AD (portal.azure.com)
  2. Ajouter les permissions Application : Mail.Read, ChannelMessage.Read.All
  3. Obtenir le consentement admin (Grant admin consent)
  4. Creer un Client Secret
  5. pip install pg8000

Variables d'environnement :
  AZURE_TENANT_ID   : ID du tenant Azure AD (GUID ou domaine)
  AZURE_CLIENT_ID   : Application (client) ID de l'App Registration
  AZURE_CLIENT_SECRET: Client secret value
  GRAPH_USER_EMAIL  : adresse email de la boite a lire (pour mode email)
  GRAPH_TEAM_ID     : ID de l'equipe Teams (pour mode teams)
  GRAPH_CHANNEL_ID  : ID du canal Teams (pour mode teams)
  GRAPH_PLATFORM    : 'email' ou 'teams' (defaut: email)
  PG_PASS           : mot de passe Supabase

Usage :
  # Import emails Outlook
  AZURE_TENANT_ID=xxx AZURE_CLIENT_ID=yyy AZURE_CLIENT_SECRET=zzz \
  GRAPH_USER_EMAIL=itsupport@beautybay.com PG_PASS=... \
  python scripts/poll_graph.py --init

  # Import messages Teams
  AZURE_TENANT_ID=xxx AZURE_CLIENT_ID=yyy AZURE_CLIENT_SECRET=zzz \
  GRAPH_PLATFORM=teams GRAPH_TEAM_ID=xxx GRAPH_CHANNEL_ID=yyy PG_PASS=... \
  python scripts/poll_graph.py --init

  # Poll incremental (derniere heure)
  python scripts/poll_graph.py
  python scripts/poll_graph.py --loop 300
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

# --- Configuration Azure AD ---
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")

# --- Configuration Graph ---
GRAPH_USER_EMAIL = os.environ.get("GRAPH_USER_EMAIL", "")
GRAPH_PLATFORM = os.environ.get("GRAPH_PLATFORM", "email")  # 'email' or 'teams'
GRAPH_TEAM_ID = os.environ.get("GRAPH_TEAM_ID", "")
GRAPH_CHANNEL_ID = os.environ.get("GRAPH_CHANNEL_ID", "")

# --- Configuration PostgreSQL ---
PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

BATCH_SIZE = 200
PAGE_SIZE = 100
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ---------------------------------------------------------------------------
# OAuth2 Client Credentials
# ---------------------------------------------------------------------------

_token_cache = {"token": None, "expires": 0}


def get_graph_token():
    """Obtient un token Graph API via client_credentials (pas de MFA)."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] > now + 60:
        return _token_cache["token"]

    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    body = (
        f"grant_type=client_credentials"
        f"&client_id={urllib.request.quote(AZURE_CLIENT_ID)}"
        f"&client_secret={urllib.request.quote(AZURE_CLIENT_SECRET)}"
        f"&scope={urllib.request.quote('https://graph.microsoft.com/.default')}"
    )
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode())

    if "access_token" not in result:
        print(f"  ! Token error: {result.get('error_description', result)}", file=sys.stderr)
        sys.exit(1)

    _token_cache["token"] = result["access_token"]
    _token_cache["expires"] = now + result.get("expires_in", 3600)
    print(f"  Graph API token acquired (expires in {result.get('expires_in', '?')}s)")
    return result["access_token"]


def graph_get(path, params=None):
    """GET against Microsoft Graph API with auto-pagination."""
    token = get_graph_token()
    url = f"{GRAPH_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url += "?" + qs

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")

    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def graph_get_all(path, params=None, max_items=0):
    """GET with automatic @odata.nextLink pagination."""
    all_items = []
    token = get_graph_token()

    url = f"{GRAPH_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url += "?" + qs

    while url:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300] if hasattr(e, 'read') else ''
            print(f"  ! HTTP {e.code}: {body}", file=sys.stderr)
            break

        items = data.get("value", [])
        all_items.extend(items)
        print(f"  ... {len(all_items)} items loaded")

        if max_items > 0 and len(all_items) >= max_items:
            all_items = all_items[:max_items]
            break

        url = data.get("@odata.nextLink")

    return all_items


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def strip_html(html):
    """Remove HTML tags and collapse whitespace."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_email(msg):
    """Normalize a Graph API mail message into an event dict."""
    subject = msg.get("subject", "") or ""
    sender_obj = msg.get("sender", {}).get("emailAddress", {})
    sender = sender_obj.get("address", sender_obj.get("name", "unknown"))

    # Body : prefer text, fallback to HTML stripped
    body_obj = msg.get("body", {})
    if body_obj.get("contentType") == "text":
        body_text = body_obj.get("content", "")
    else:
        body_text = strip_html(body_obj.get("content", ""))

    message_id = msg.get("internetMessageId", msg.get("id", ""))
    if message_id:
        message_id = message_id.strip().strip("<>")

    return {
        "platform": "email",
        "external_id": GRAPH_USER_EMAIL.lower(),
        "source_message_id": message_id[:500],
        "sender_raw": sender[:500],
        "body": f"[{subject}] {body_text}"[:2000],
    }


def fetch_emails_all():
    """Fetch ALL emails from a mailbox (init mode)."""
    print(f"  Fetching all emails for {GRAPH_USER_EMAIL}...")
    # Fetch from multiple folders
    all_events = []

    for folder_name in ["inbox", "sentitems"]:
        print(f"\n  --- Folder: {folder_name} ---")
        messages = graph_get_all(
            f"/users/{GRAPH_USER_EMAIL}/mailFolders/{folder_name}/messages",
            params={
                "$top": PAGE_SIZE,
                "$select": "id,internetMessageId,subject,sender,body,receivedDateTime",
                "$orderby": "receivedDateTime desc",
            }
        )
        for msg in messages:
            all_events.append(normalize_email(msg))

    return all_events


def fetch_emails_recent(hours=1):
    """Fetch emails received in the last N hours."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"  Fetching emails since {since} for {GRAPH_USER_EMAIL}...")

    messages = graph_get_all(
        f"/users/{GRAPH_USER_EMAIL}/messages",
        params={
            "$top": PAGE_SIZE,
            "$select": "id,internetMessageId,subject,sender,body,receivedDateTime",
            "$filter": f"receivedDateTime ge {since}",
            "$orderby": "receivedDateTime desc",
        }
    )
    return [normalize_email(msg) for msg in messages]


# ---------------------------------------------------------------------------
# Teams helpers
# ---------------------------------------------------------------------------

def normalize_teams_message(msg, channel_name=""):
    """Normalize a Graph API Teams message into an event dict."""
    sender_obj = msg.get("from", {})
    user_obj = sender_obj.get("user", {}) if sender_obj else {}
    sender = user_obj.get("displayName", user_obj.get("id", "unknown"))

    body_obj = msg.get("body", {})
    if body_obj.get("contentType") == "text":
        body_text = body_obj.get("content", "")
    else:
        body_text = strip_html(body_obj.get("content", ""))

    subject = msg.get("subject", "") or channel_name
    message_id = msg.get("id", "")

    return {
        "platform": "teams",
        "external_id": GRAPH_USER_EMAIL.lower() if GRAPH_USER_EMAIL else GRAPH_TEAM_ID,
        "source_message_id": message_id[:500],
        "sender_raw": sender[:500],
        "body": f"[{subject}] {body_text}"[:2000],
    }


def list_teams():
    """List all teams the app can see."""
    data = graph_get("/teams", params={"$top": "100"})
    return data.get("value", [])


def list_channels(team_id):
    """List channels in a team."""
    data = graph_get(f"/teams/{team_id}/channels")
    return data.get("value", [])


def fetch_teams_all():
    """Fetch ALL messages from a Teams channel (init mode)."""
    team_id = GRAPH_TEAM_ID
    channel_id = GRAPH_CHANNEL_ID

    if not team_id or not channel_id:
        # Auto-discover : list teams and channels
        print("  No GRAPH_TEAM_ID/GRAPH_CHANNEL_ID set, discovering...")
        teams = list_teams()
        if not teams:
            print("  ! No teams found. Check permissions.", file=sys.stderr)
            return []
        print(f"  Found {len(teams)} team(s):")
        for t in teams:
            print(f"    {t['id']} : {t.get('displayName', '?')}")

        # Use first team if not specified
        if not team_id:
            team_id = teams[0]["id"]
            print(f"  Using team: {teams[0].get('displayName', team_id)}")

        channels = list_channels(team_id)
        if not channels:
            print("  ! No channels found.", file=sys.stderr)
            return []
        print(f"  Found {len(channels)} channel(s):")
        for c in channels:
            print(f"    {c['id']} : {c.get('displayName', '?')}")

        if not channel_id:
            # Use General channel or first
            general = [c for c in channels if c.get("displayName", "").lower() == "general"]
            channel_id = general[0]["id"] if general else channels[0]["id"]

    print(f"  Fetching messages from team={team_id} channel={channel_id}...")
    messages = graph_get_all(
        f"/teams/{team_id}/channels/{channel_id}/messages",
        params={"$top": 50}
    )

    # Also fetch replies for each message
    all_events = []
    for msg in messages:
        if msg.get("messageType") == "message":
            all_events.append(normalize_teams_message(msg))

        # Fetch replies
        msg_id = msg.get("id")
        if msg_id:
            try:
                replies = graph_get_all(
                    f"/teams/{team_id}/channels/{channel_id}/messages/{msg_id}/replies",
                    params={"$top": 50}
                )
                for reply in replies:
                    if reply.get("messageType") == "message":
                        all_events.append(normalize_teams_message(reply))
            except Exception as e:
                print(f"  ! Replies for {msg_id}: {e}", file=sys.stderr)

    return all_events


def fetch_teams_recent(hours=1):
    """Fetch recent Teams messages (last N hours)."""
    # Graph API doesn't support $filter on channel messages easily,
    # so we fetch recent pages and filter client-side
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    team_id = GRAPH_TEAM_ID
    channel_id = GRAPH_CHANNEL_ID
    if not team_id or not channel_id:
        print("  ! GRAPH_TEAM_ID and GRAPH_CHANNEL_ID required for incremental poll", file=sys.stderr)
        return []

    messages = graph_get_all(
        f"/teams/{team_id}/channels/{channel_id}/messages",
        params={"$top": 50},
        max_items=200,
    )

    recent = []
    for msg in messages:
        created = msg.get("createdDateTime", "")
        if created:
            try:
                msg_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if msg_time < since:
                    continue
            except ValueError:
                pass
        if msg.get("messageType") == "message":
            recent.append(normalize_teams_message(msg))

    return recent


# ---------------------------------------------------------------------------
# PostgreSQL
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
                print(f"  + {ev['source_message_id'][:40]:40s} {ev['body'][:60]}")
        except Exception as e:
            print(f"  ! {ev['source_message_id'][:40]}: {e}", file=sys.stderr)
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

def poll_init():
    """Full import of all emails or Teams messages."""
    if GRAPH_PLATFORM == "teams":
        print(f"[{time.strftime('%H:%M:%S')}] === IMPORT INITIAL Teams (Graph API) ===")
        events = fetch_teams_all()
    else:
        print(f"[{time.strftime('%H:%M:%S')}] === IMPORT INITIAL Outlook (Graph API, {GRAPH_USER_EMAIL}) ===")
        events = fetch_emails_all()

    print(f"\n  Total: {len(events)} messages retrieved")
    if events:
        inserted = insert_events(events)
        print(f"  === {inserted} new events inserted (out of {len(events)}) ===")
    return len(events)


def poll_incremental():
    """Incremental poll (last hour)."""
    if GRAPH_PLATFORM == "teams":
        print(f"[{time.strftime('%H:%M:%S')}] Polling Teams (Graph API)...")
        events = fetch_teams_recent(hours=1)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Polling Outlook (Graph API, {GRAPH_USER_EMAIL})...")
        events = fetch_emails_recent(hours=1)

    if not events:
        print("  No new messages")
        return 0

    inserted = insert_events(events)
    print(f"  {inserted} new events inserted")
    return inserted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not AZURE_TENANT_ID or not AZURE_CLIENT_ID or not AZURE_CLIENT_SECRET:
        print("Variables manquantes. Configurez :", file=sys.stderr)
        print("  AZURE_TENANT_ID   = ID du tenant (GUID ou domaine)", file=sys.stderr)
        print("  AZURE_CLIENT_ID   = Application (client) ID", file=sys.stderr)
        print("  AZURE_CLIENT_SECRET = Client secret", file=sys.stderr)
        print("", file=sys.stderr)
        print("Pour creer l'App Registration :", file=sys.stderr)
        print("  1. portal.azure.com > Azure Active Directory > App registrations > New", file=sys.stderr)
        print("  2. API permissions > Add > Microsoft Graph > Application permissions :", file=sys.stderr)
        print("     - Mail.Read (pour Outlook)", file=sys.stderr)
        print("     - ChannelMessage.Read.All (pour Teams)", file=sys.stderr)
        print("  3. Grant admin consent", file=sys.stderr)
        print("  4. Certificates & secrets > New client secret", file=sys.stderr)
        sys.exit(1)

    if not PG_PASS:
        print("PG_PASS manquant. set PG_PASS=...", file=sys.stderr)
        sys.exit(1)

    if GRAPH_PLATFORM == "email" and not GRAPH_USER_EMAIL:
        print("GRAPH_USER_EMAIL manquant (adresse de la boite a lire).", file=sys.stderr)
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
