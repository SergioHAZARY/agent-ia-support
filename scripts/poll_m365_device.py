# -*- coding: utf-8 -*-
"""
Polling Microsoft 365 (Outlook + Teams) via Graph API + Device Code Flow.

Contourne MFA sans App Registration Azure : l'utilisateur se connecte
une seule fois dans le navigateur, le script obtient un token et lit
les emails/messages via Graph API.

Prerequis : pip install pg8000

Fonctionnement :
  1. Le script affiche un code et une URL
  2. Vous ouvrez l'URL dans le navigateur, entrez le code, vous connectez (MFA OK)
  3. Le script recoit le token et commence l'import
  4. Le token est sauvegarde dans un fichier local pour les prochains polls

Mode --init   : import de TOUS les emails (inbox + sent)
Mode --loop N : poll continu toutes les N secondes (defaut 300)
Mode par defaut : un seul poll incremental (derniere heure)

Usage :
  # Outlook BeautyBay
  M365_USER=itsupport@beautybay.com PG_PASS=... python scripts/poll_m365_device.py --init

  # Outlook Odoo
  M365_USER=support-odoo@regardbeauty.onmicrosoft.com PG_PASS=... python scripts/poll_m365_device.py --init

  # Teams Atlas For Men
  M365_USER=THAINA_AA@atlasformen.com M365_MODE=teams PG_PASS=... python scripts/poll_m365_device.py --init
"""
import json
import os
import re
import sys
import base64
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Configuration ---
M365_USER = os.environ.get("M365_USER", "")
M365_MODE = os.environ.get("M365_MODE", "email")  # 'email' or 'teams'

# Azure public client ID (Outlook Mobile) — typically pre-authorized on all tenants.
# Fallback chain: Outlook Mobile > Teams > Azure CLI
CLIENT_ID = os.environ.get("M365_CLIENT_ID", "27922004-5251-4030-b22d-91ecd9a37ea4")

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

BATCH_SIZE = 200
PAGE_SIZE = 100
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Token cache file (per user)
TOKEN_DIR = Path(__file__).parent / ".tokens"


# ---------------------------------------------------------------------------
# OAuth2 Device Code Flow
# ---------------------------------------------------------------------------

def _token_file():
    safe_name = re.sub(r'[^\w@.]', '_', M365_USER.lower())
    return TOKEN_DIR / f"token_{safe_name}.json"


def _save_token(token_data):
    TOKEN_DIR.mkdir(exist_ok=True)
    token_data["_saved_at"] = time.time()
    with open(_token_file(), "w") as f:
        json.dump(token_data, f)


def _load_token():
    path = _token_file()
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data
    except Exception:
        return None


def _detect_tenant(email):
    """Detect tenant from email domain via OpenID discovery."""
    domain = email.split("@")[-1]
    url = f"https://login.microsoftonline.com/{domain}/.well-known/openid-configuration"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            # Extract tenant ID from issuer or token_endpoint
            endpoint = data.get("token_endpoint", "")
            # https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
            parts = endpoint.split("/")
            for i, p in enumerate(parts):
                if p == "login.microsoftonline.com" and i + 1 < len(parts):
                    return parts[i + 1]
    except Exception:
        pass
    return domain


def _refresh_token(token_data):
    """Refresh an expired token using the refresh_token."""
    refresh = token_data.get("refresh_token")
    if not refresh:
        return None

    tenant = _detect_tenant(M365_USER)
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    body = (
        f"grant_type=refresh_token"
        f"&client_id={urllib.request.quote(CLIENT_ID)}"
        f"&refresh_token={urllib.request.quote(refresh)}"
        f"&scope={urllib.request.quote('Mail.Read Mail.ReadBasic offline_access')}"
    )
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())
        if "access_token" in result:
            print(f"  Token refreshed for {M365_USER}")
            _save_token(result)
            return result
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:300]
        print(f"  ! Refresh failed: {body_err}", file=sys.stderr)
    return None


def get_token():
    """Get a valid Graph API token, refreshing or re-authenticating as needed."""
    cached = _load_token()
    if cached:
        saved_at = cached.get("_saved_at", 0)
        expires_in = cached.get("expires_in", 3600)
        if time.time() < saved_at + expires_in - 120:
            return cached["access_token"]
        # Try refresh
        refreshed = _refresh_token(cached)
        if refreshed:
            return refreshed["access_token"]

    # Device code flow
    return _device_code_auth()


def _device_code_auth():
    """Interactive device code authentication.
    Tries multiple client_id + scope combos to find one the tenant accepts."""
    tenant = _detect_tenant(M365_USER)
    print(f"  Tenant detected: {tenant}")

    # Combos to try, in order of likelihood of success.
    # EWS scope uses a different resource than Graph, often pre-authorized.
    combos = [
        # EWS first — Graph resource is blocked on many tenants (AADSTS65002)
        ("Outlook Mobile + EWS",   "27922004-5251-4030-b22d-91ecd9a37ea4", "https://outlook.office365.com/EWS.AccessAsUser.All offline_access", "ews"),
        ("Azure CLI + EWS",        "04b07795-8ddb-461a-bbee-02f9e1bf7b46", "https://outlook.office365.com/EWS.AccessAsUser.All offline_access", "ews"),
        ("PowerShell + EWS",       "1b730954-1685-4b74-9bfd-dac224a7b894", "https://outlook.office365.com/EWS.AccessAsUser.All offline_access", "ews"),
        # IMAP fallback
        ("Azure CLI + IMAP",       "04b07795-8ddb-461a-bbee-02f9e1bf7b46", "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access", "imap"),
        ("Outlook Mobile + IMAP",  "27922004-5251-4030-b22d-91ecd9a37ea4", "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access", "imap"),
        # Graph last (often blocked)
        ("Outlook Mobile + Graph", "27922004-5251-4030-b22d-91ecd9a37ea4", "Mail.Read Mail.ReadBasic offline_access", "graph"),
        ("Teams + Graph",          "1fec8e78-bce4-4aaf-ab1b-5451cc387264", "Mail.Read offline_access", "graph"),
    ]
    if M365_MODE == "teams":
        combos = [
            ("Teams + Graph",      "1fec8e78-bce4-4aaf-ab1b-5451cc387264", "Chat.Read offline_access", "graph"),
            ("Outlook Mobile",     "27922004-5251-4030-b22d-91ecd9a37ea4", "Chat.Read offline_access", "graph"),
            ("Azure CLI",          "04b07795-8ddb-461a-bbee-02f9e1bf7b46", "Chat.Read offline_access", "graph"),
        ]

    # Step 1: Request device code (just pick first working combo)
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"
    device = None
    chosen_client_id = CLIENT_ID
    chosen_api = "graph"

    for name, cid, scope, api in combos:
        body = f"client_id={urllib.request.quote(cid)}&scope={urllib.request.quote(scope)}"
        req = urllib.request.Request(url, data=body.encode(), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                device = json.loads(r.read().decode())
                chosen_client_id = cid
                chosen_api = api
                print(f"  Using: {name} (api={api})")
                break
        except urllib.error.HTTPError:
            continue

    if not device:
        print("  ! No working client_id/scope combo found for this tenant.", file=sys.stderr)
        sys.exit(1)

    user_code = device["user_code"]
    verify_url = device["verification_uri"]
    device_code = device["device_code"]
    interval = device.get("interval", 5)
    expires_in = device.get("expires_in", 900)

    print()
    print("  +----------------------------------------------------------+")
    print(f"  |  Ouvrez : {verify_url:46s} |")
    print(f"  |  Entrez le code : {user_code:38s} |")
    print(f"  |  Connectez-vous avec : {M365_USER:33s} |")
    print("  +----------------------------------------------------------+")
    print()
    print(f"  En attente de validation (expire dans {expires_in}s)...")

    # Step 2: Poll for token
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    token_body = (
        f"grant_type=urn:ietf:params:oauth:grant-type:device_code"
        f"&client_id={urllib.request.quote(chosen_client_id)}"
        f"&device_code={urllib.request.quote(device_code)}"
    )

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        req = urllib.request.Request(token_url, data=token_body.encode(), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read().decode())
            if "access_token" in result:
                print(f"\n  Authentification reussie pour {M365_USER} ! (api={chosen_api})")
                result["_api_mode"] = chosen_api
                result["_client_id"] = chosen_client_id
                _save_token(result)
                return result["access_token"]
        except urllib.error.HTTPError as e:
            err = json.loads(e.read().decode())
            error_code = err.get("error", "")
            if error_code == "authorization_pending":
                sys.stdout.write(".")
                sys.stdout.flush()
                continue
            elif error_code == "slow_down":
                interval += 5
                continue
            elif error_code == "expired_token":
                print("\n  ! Code expire. Relancez le script.", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"\n  ! Erreur: {err.get('error_description', error_code)}", file=sys.stderr)
                sys.exit(1)

    print("\n  ! Timeout. Relancez le script.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Graph API helpers
# ---------------------------------------------------------------------------

def graph_get_all(path, params=None, max_items=0):
    """GET with automatic @odata.nextLink pagination."""
    all_items = []
    token = get_token()

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
            body_err = ""
            try:
                body_err = e.read().decode()[:300]
            except Exception:
                pass
            print(f"  ! HTTP {e.code}: {body_err}", file=sys.stderr)
            if e.code == 401:
                # Token expired mid-run, clear cache and retry once
                _token_file().unlink(missing_ok=True)
                print("  Token expired, please re-run the script.", file=sys.stderr)
            break

        items = data.get("value", [])
        all_items.extend(items)

        if len(all_items) % 500 < len(items):
            print(f"  ... {len(all_items)} items loaded")

        if max_items > 0 and len(all_items) >= max_items:
            all_items = all_items[:max_items]
            break

        url = data.get("@odata.nextLink")

    return all_items


# ---------------------------------------------------------------------------
# Email normalization
# ---------------------------------------------------------------------------

def strip_html(html):
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_email(msg):
    subject = msg.get("subject", "") or ""
    sender_obj = msg.get("sender", {}).get("emailAddress", {})
    sender = sender_obj.get("address", sender_obj.get("name", "unknown"))

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
        "external_id": M365_USER.lower(),
        "source_message_id": message_id[:500],
        "sender_raw": sender[:500],
        "body": f"[{subject}] {body_text}"[:2000],
    }


def normalize_teams_message(msg):
    sender_obj = msg.get("from", {})
    user_obj = sender_obj.get("user", {}) if sender_obj else {}
    sender = user_obj.get("displayName", user_obj.get("id", "unknown"))

    body_obj = msg.get("body", {})
    if body_obj.get("contentType") == "text":
        body_text = body_obj.get("content", "")
    else:
        body_text = strip_html(body_obj.get("content", ""))

    subject = msg.get("subject", "") or ""
    message_id = msg.get("id", "")

    return {
        "platform": "teams",
        "external_id": M365_USER.lower(),
        "source_message_id": message_id[:500],
        "sender_raw": sender[:500],
        "body": f"[{subject}] {body_text}"[:2000],
    }


# ---------------------------------------------------------------------------
# EWS (Exchange Web Services) helpers — fallback when Graph consent is blocked
# ---------------------------------------------------------------------------

EWS_URL = "https://outlook.office365.com/EWS/Exchange.asmx"


def _ews_request(token, soap_body):
    """Send a SOAP request to EWS and return the XML response as string."""
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"'
        ' xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages">'
        '<soap:Header>'
        '<t:RequestServerVersion Version="Exchange2016"/>'
        '</soap:Header>'
        '<soap:Body>' + soap_body + '</soap:Body>'
        '</soap:Envelope>'
    )
    req = urllib.request.Request(EWS_URL, data=envelope.encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "text/xml; charset=utf-8")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def _ews_extract_items(xml_text):
    """Extract email items from EWS FindItem/GetItem XML response (simple regex parser)."""
    items = []
    # Split on <t:Message> or <t:Items><t:Message>
    for block in re.split(r'<t:Message\b', xml_text)[1:]:
        block = block.split('</t:Message>')[0]

        subject = ""
        m = re.search(r'<t:Subject>([^<]*)</t:Subject>', block)
        if m:
            subject = m.group(1)

        sender = ""
        m = re.search(r'<t:Sender>.*?<t:EmailAddress>([^<]*)</t:EmailAddress>.*?</t:Sender>', block, re.DOTALL)
        if m:
            sender = m.group(1)

        msg_id = ""
        m = re.search(r'<t:InternetMessageId>([^<]*)</t:InternetMessageId>', block)
        if m:
            msg_id = m.group(1).strip().strip("<>")
        if not msg_id:
            m = re.search(r'<t:ItemId Id="([^"]*)"', block)
            if m:
                msg_id = m.group(1)

        body_text = ""
        m = re.search(r'<t:Body[^>]*>([^<]*)</t:Body>', block)
        if m:
            body_text = strip_html(m.group(1))

        if msg_id:
            items.append({
                "platform": "email",
                "external_id": M365_USER.lower(),
                "source_message_id": msg_id[:500],
                "sender_raw": sender[:500],
                "body": f"[{subject}] {body_text}"[:2000],
            })
    return items


def ews_fetch_folder(token, folder_name="inbox", max_items=0):
    """Fetch all emails from a folder via EWS FindItem + GetItem."""
    all_events = []
    offset = 0
    page = 200

    # Map friendly names to EWS DistinguishedFolderId
    folder_map = {"inbox": "inbox", "sentitems": "sentitems", "sent": "sentitems"}
    ews_folder = folder_map.get(folder_name.lower(), folder_name.lower())

    while True:
        soap = (
            f'<m:FindItem Traversal="Shallow">'
            f'<m:ItemShape><t:BaseShape>Default</t:BaseShape>'
            f'<t:AdditionalProperties>'
            f'<t:FieldURI FieldURI="item:Subject"/>'
            f'<t:FieldURI FieldURI="message:Sender"/>'
            f'<t:FieldURI FieldURI="message:InternetMessageId"/>'
            f'<t:FieldURI FieldURI="item:DateTimeReceived"/>'
            f'</t:AdditionalProperties>'
            f'</m:ItemShape>'
            f'<m:IndexedPageItemView MaxEntriesReturned="{page}" Offset="{offset}" BasePoint="Beginning"/>'
            f'<m:ParentFolderIds>'
            f'<t:DistinguishedFolderId Id="{ews_folder}"/>'
            f'</m:ParentFolderIds>'
            f'</m:FindItem>'
        )
        try:
            xml = _ews_request(token, soap)
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:300] if hasattr(e, 'read') else str(e)
            print(f"  ! EWS FindItem error ({e.code}): {err}", file=sys.stderr)
            break

        items = _ews_extract_items(xml)
        all_events.extend(items)
        print(f"  ... {len(all_events)} emails loaded from {ews_folder}")

        if not items or len(items) < page:
            break
        offset += page
        if max_items > 0 and len(all_events) >= max_items:
            break

    return all_events


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------

def _get_api_mode():
    """Check which API mode was used for auth (graph, ews, or imap)."""
    cached = _load_token()
    return (cached or {}).get("_api_mode", "graph")


def fetch_emails_all():
    """Fetch all emails from inbox + sent."""
    api = _get_api_mode()

    if api == "ews":
        token = get_token()
        all_events = []
        for folder in ["inbox", "sentitems"]:
            print(f"\n  --- Folder: {folder} (EWS) ---")
            events = ews_fetch_folder(token, folder)
            all_events.extend(events)
        return all_events

    if api == "imap":
        # Fallback to IMAP with OAuth2 token
        return _fetch_emails_imap_oauth()

    # Default: Graph API
    all_events = []
    for folder in ["inbox", "sentitems"]:
        print(f"\n  --- Folder: {folder} (Graph) ---")
        messages = graph_get_all(
            f"/me/mailFolders/{folder}/messages",
            params={
                "$top": PAGE_SIZE,
                "$select": "id,internetMessageId,subject,sender,body,receivedDateTime",
                "$orderby": "receivedDateTime desc",
            }
        )
        print(f"  {len(messages)} messages in {folder}")
        for msg in messages:
            all_events.append(normalize_email(msg))
    return all_events


def fetch_emails_recent(hours=1):
    api = _get_api_mode()

    if api == "ews":
        # EWS doesn't easily filter by date in FindItem, just get recent page
        token = get_token()
        return ews_fetch_folder(token, "inbox", max_items=200)

    if api == "imap":
        return _fetch_emails_imap_oauth()

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"  Emails since {since}...")
    messages = graph_get_all(
        "/me/messages",
        params={
            "$top": PAGE_SIZE,
            "$select": "id,internetMessageId,subject,sender,body,receivedDateTime",
            "$filter": f"receivedDateTime ge {since}",
            "$orderby": "receivedDateTime desc",
        }
    )
    return [normalize_email(msg) for msg in messages]


def _fetch_emails_imap_oauth():
    """Fetch emails via IMAP using OAuth2 token."""
    import imaplib
    import email as email_lib
    import email.header

    token = get_token()
    auth_string = f"user={M365_USER}\x01auth=Bearer {token}\x01\x01"
    encoded = base64.b64encode(auth_string.encode()).decode()

    imap = imaplib.IMAP4_SSL("outlook.office365.com", 993)
    try:
        imap.authenticate("XOAUTH2", lambda x: encoded.encode())
    except imaplib.IMAP4.error as e:
        print(f"  ! IMAP XOAUTH2 failed: {e}", file=sys.stderr)
        return []

    all_events = []
    for folder in ["INBOX", "Sent Items"]:
        quoted = f'"{folder}"' if " " in folder else folder
        status, _ = imap.select(quoted, readonly=True)
        if status != "OK":
            continue
        status, data = imap.search(None, "ALL")
        if status != "OK":
            continue
        msg_ids = data[0].split()
        print(f"  {folder}: {len(msg_ids)} messages (IMAP)")
        for mid in msg_ids:
            try:
                status, msg_data = imap.fetch(mid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)
                subject = ""
                raw_subj = msg.get("Subject", "")
                if raw_subj:
                    parts = email.header.decode_header(raw_subj)
                    decoded = []
                    for d, ch in parts:
                        if isinstance(d, bytes):
                            decoded.append(d.decode(ch or "utf-8", errors="replace"))
                        else:
                            decoded.append(d)
                    subject = " ".join(decoded)
                from_addr = msg.get("From", "unknown")
                message_id = msg.get("Message-ID", f"imap-{mid.decode()}")
                message_id = message_id.strip().strip("<>")
                all_events.append({
                    "platform": "email",
                    "external_id": M365_USER.lower(),
                    "source_message_id": message_id[:500],
                    "sender_raw": from_addr[:500],
                    "body": f"[{subject}]"[:2000],
                })
            except Exception as e:
                print(f"  ! msg {mid}: {e}", file=sys.stderr)
    imap.logout()
    return all_events


def fetch_teams_chats_all():
    """Fetch all Teams chat messages (1:1 and group chats, NOT channels).
    Uses /me/chats which works with delegated Chat.Read permission."""
    print("  Fetching Teams chats...")
    chats = graph_get_all("/me/chats", params={"$top": 50})
    print(f"  {len(chats)} chat(s) found")

    all_events = []
    for chat in chats:
        chat_id = chat.get("id")
        topic = chat.get("topic") or chat.get("chatType", "chat")
        try:
            messages = graph_get_all(
                f"/me/chats/{chat_id}/messages",
                params={"$top": 50},
                max_items=500,
            )
            for msg in messages:
                if msg.get("messageType") == "message" and msg.get("body", {}).get("content"):
                    ev = normalize_teams_message(msg)
                    ev["body"] = f"[{topic}] " + ev["body"]
                    ev["body"] = ev["body"][:2000]
                    all_events.append(ev)
        except Exception as e:
            print(f"  ! Chat {chat_id}: {e}", file=sys.stderr)

    return all_events


def fetch_teams_chats_recent(hours=1):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    all_events = fetch_teams_chats_all()
    # Client-side filter (Graph doesn't support $filter on chat messages well)
    return all_events  # Return all for now; dedup handles the rest


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
                if inserted % 50 == 1 or inserted <= 5:
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
    if M365_MODE == "teams":
        print(f"[{time.strftime('%H:%M:%S')}] === IMPORT Teams ({M365_USER}) ===")
        events = fetch_teams_chats_all()
    else:
        print(f"[{time.strftime('%H:%M:%S')}] === IMPORT Outlook ({M365_USER}) ===")
        events = fetch_emails_all()

    print(f"\n  Total: {len(events)} messages")
    if events:
        inserted = insert_events(events)
        print(f"  === {inserted} new events inserted (out of {len(events)}) ===")
    return len(events)


def poll_incremental():
    if M365_MODE == "teams":
        print(f"[{time.strftime('%H:%M:%S')}] Polling Teams ({M365_USER})...")
        events = fetch_teams_chats_recent(hours=1)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Polling Outlook ({M365_USER})...")
        events = fetch_emails_recent(hours=1)

    if not events:
        print("  No new messages")
        return 0

    inserted = insert_events(events)
    print(f"  {inserted} new events inserted")
    return inserted


def main():
    if not M365_USER:
        print("M365_USER manquant (adresse email du compte M365).", file=sys.stderr)
        print("  M365_USER=itsupport@beautybay.com python scripts/poll_m365_device.py --init", file=sys.stderr)
        sys.exit(1)
    if not PG_PASS:
        print("PG_PASS manquant.", file=sys.stderr)
        sys.exit(1)

    try:
        import pg8000  # noqa
    except ImportError:
        print("ERREUR: pip install pg8000", file=sys.stderr)
        sys.exit(1)

    # Test token upfront
    print(f"  Mode: {M365_MODE} | User: {M365_USER}")
    get_token()

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
                print(f"  ! Error: {e}", file=sys.stderr)
            time.sleep(interval)
    else:
        poll_incremental()


if __name__ == "__main__":
    main()
