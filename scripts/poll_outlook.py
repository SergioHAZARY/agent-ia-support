# -*- coding: utf-8 -*-
"""
Polling Microsoft 365 Outlook (IMAP) -> Supabase (table events).

Recupere les emails via IMAP sur outlook.office365.com.
Supporte OAuth2 XOAUTH2 (via Microsoft Graph API) quand Basic Auth
est desactive (cas par defaut sur M365 depuis 2023).

Prerequis : pip install pg8000 msal

Mode --init   : import de TOUS les emails (INBOX + Sent Items + autres)
Mode --loop N : poll continu toutes les N secondes (defaut 300)
Mode par defaut : un seul poll incremental (emails recus < 1 jour)

Usage :
  set OUTLOOK_EMAIL=itsupport@beautybay.com
  set OUTLOOK_PASS=...
  set PG_PASS=...
  python scripts/poll_outlook.py --init
  python scripts/poll_outlook.py --loop 300
  python scripts/poll_outlook.py
"""
import base64
import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# --- Configuration ---
OUTLOOK_EMAIL = os.environ.get("OUTLOOK_EMAIL", "itsupport@beautybay.com")
OUTLOOK_PASS = os.environ.get("OUTLOOK_PASS", "")
IMAP_SERVER = os.environ.get("IMAP_SERVER", "outlook.office365.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

DEPARTMENT = os.environ.get("DEPARTMENT", "beautybay")
BATCH_SIZE = 200


def get_pg_conn():
    import pg8000
    return pg8000.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DB,
        user=PG_USER, password=PG_PASS, ssl_context=True
    )


def decode_header_value(raw):
    """Decode un en-tete MIME (RFC 2047)."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def extract_body(msg):
    """Extrait le texte brut du message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback : HTML
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    return text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def fetch_emails_imap(imap, folder="INBOX", search_criteria="ALL", max_emails=0):
    """Recupere les emails depuis un dossier IMAP."""
    emails = []
    try:
        # Microsoft 365 : les noms de dossiers avec espaces doivent etre entre guillemets
        quoted_folder = f'"{folder}"' if " " in folder else folder
        status, _ = imap.select(quoted_folder, readonly=True)
        if status != "OK":
            print(f"  ! Cannot open folder: {folder}", file=sys.stderr)
            return emails
    except Exception as e:
        print(f"  ! Error selecting folder {folder}: {e}", file=sys.stderr)
        return emails

    status, data = imap.search(None, search_criteria)
    if status != "OK":
        return emails

    msg_ids = data[0].split()
    if not msg_ids:
        return emails

    print(f"  {folder}: {len(msg_ids)} messages found")
    if max_emails > 0:
        msg_ids = msg_ids[-max_emails:]

    for i, mid in enumerate(msg_ids):
        try:
            status, msg_data = imap.fetch(mid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = decode_header_value(msg.get("Subject", ""))
            from_addr = decode_header_value(msg.get("From", ""))
            message_id = msg.get("Message-ID", f"outlook-{folder}-{mid.decode()}")
            date_str = msg.get("Date", "")

            body = extract_body(msg)
            if not body:
                body = subject

            # Nettoyer le message_id
            message_id = message_id.strip().strip("<>")
            if not message_id:
                message_id = f"outlook-{folder}-{mid.decode()}"

            emails.append({
                "platform": "email",
                "external_id": OUTLOOK_EMAIL.lower(),
                "source_message_id": message_id[:500],
                "sender_raw": from_addr[:500],
                "body": f"[{subject}] {body}"[:2000],
            })

            if (i + 1) % 100 == 0:
                print(f"  ... {i + 1}/{len(msg_ids)} messages read")
        except Exception as e:
            print(f"  ! Message {mid}: {e}", file=sys.stderr)

    return emails


def list_folders(imap):
    """Liste tous les dossiers IMAP disponibles."""
    status, folders = imap.list()
    if status != "OK":
        return []
    result = []
    for f in folders:
        # Format: (\\flags) "delimiter" "name"
        decoded = f.decode() if isinstance(f, bytes) else f
        match = re.search(r'"([^"]+)"$|(\S+)$', decoded)
        if match:
            name = match.group(1) or match.group(2)
            result.append(name)
    return result


def _get_oauth2_token():
    """Obtient un token OAuth2 via ROPC (username/password) pour IMAP."""
    # Client IDs connus pour ROPC IMAP :
    # - Azure CLI public client (pre-authorized pour la plupart des tenants)
    client_ids = [
        ("Azure CLI", "04b07795-8ddb-461a-bbee-02f9e1bf7b46"),
        ("Microsoft Office", "d3590ed6-52b1-4102-aeff-aad2292ab01c"),
        ("PowerShell", "1b730954-1685-4b74-9bfd-dac224a7b894"),
    ]
    # Scope IMAP pour M365
    scope = "https://outlook.office365.com/.default"

    for name, client_id in client_ids:
        url = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
        body = (
            f"grant_type=password"
            f"&client_id={client_id}"
            f"&username={urllib.request.quote(OUTLOOK_EMAIL)}"
            f"&password={urllib.request.quote(OUTLOOK_PASS)}"
            f"&scope={urllib.request.quote(scope)}"
        )
        req = urllib.request.Request(url, data=body.encode(), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read().decode())
                if "access_token" in result:
                    print(f"  OAuth2 token acquired via {name}")
                    return result["access_token"]
        except urllib.error.HTTPError:
            continue
    return None


def _xoauth2_string(user, token):
    """Construit la chaine XOAUTH2 pour IMAP AUTHENTICATE."""
    auth_str = f"user={user}\x01auth=Bearer {token}\x01\x01"
    return base64.b64encode(auth_str.encode()).decode()


def connect_imap():
    """Connexion IMAP avec SSL. Essaie Basic Auth puis OAuth2 XOAUTH2."""
    print(f"  Connecting to IMAP {IMAP_SERVER}:{IMAP_PORT} as {OUTLOOK_EMAIL}...")
    imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)

    # Tentative 1 : Basic Auth (fonctionne si active ou App Password)
    try:
        imap.login(OUTLOOK_EMAIL, OUTLOOK_PASS)
        print("  Connected (Basic Auth).")
        return imap
    except imaplib.IMAP4.error as e:
        err_msg = str(e)
        if "basic" not in err_msg.lower() and "disabled" not in err_msg.lower():
            # Erreur non liee a Basic Auth (mauvais mot de passe, etc.)
            print(f"  ! IMAP login failed: {err_msg}", file=sys.stderr)
            raise
        print(f"  Basic Auth disabled, trying OAuth2 XOAUTH2...")

    # Tentative 2 : OAuth2 ROPC -> XOAUTH2
    token = _get_oauth2_token()
    if not token:
        print("  ! OAuth2 ROPC failed for all known client IDs.", file=sys.stderr)
        print("  Fallback: trying MSAL library...", file=sys.stderr)
        # Tentative 3 : MSAL library si installee
        try:
            import msal
            for name, client_id in [
                ("Azure CLI", "04b07795-8ddb-461a-bbee-02f9e1bf7b46"),
                ("PowerShell", "1b730954-1685-4b74-9bfd-dac224a7b894"),
            ]:
                app = msal.PublicClientApplication(
                    client_id,
                    authority="https://login.microsoftonline.com/organizations"
                )
                result = app.acquire_token_by_username_password(
                    OUTLOOK_EMAIL, OUTLOOK_PASS,
                    scopes=["https://outlook.office365.com/IMAP.AccessAsUser.All"]
                )
                if "access_token" in result:
                    token = result["access_token"]
                    print(f"  OAuth2 token acquired via MSAL ({name})")
                    break
        except ImportError:
            pass

    if not token:
        print("  ! Could not obtain OAuth2 token.", file=sys.stderr)
        print("  Solutions:", file=sys.stderr)
        print("  1. Use App Password: https://mysignins.microsoft.com/security-info", file=sys.stderr)
        print("  2. Create Azure AD App with Mail.Read permission", file=sys.stderr)
        print("  3. Set AZURE_CLIENT_ID + AZURE_CLIENT_SECRET env vars", file=sys.stderr)
        sys.exit(1)

    # Connexion IMAP avec XOAUTH2
    imap2 = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    auth_string = _xoauth2_string(OUTLOOK_EMAIL, token)
    try:
        imap2.authenticate("XOAUTH2", lambda x: auth_string.encode())
        print("  Connected (OAuth2 XOAUTH2).")
        return imap2
    except imaplib.IMAP4.error as e:
        print(f"  ! XOAUTH2 auth failed: {e}", file=sys.stderr)
        print("  The tenant may require admin consent for IMAP access.", file=sys.stderr)
        print("  Solutions:", file=sys.stderr)
        print("  1. Use App Password: https://mysignins.microsoft.com/security-info", file=sys.stderr)
        print("  2. Admin must enable IMAP in Exchange Online", file=sys.stderr)
        sys.exit(1)


def insert_events(events):
    """Insere les events dans Supabase avec reconnexion par batch."""
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


def poll_init():
    """Import initial : TOUS les emails de tous les dossiers pertinents."""
    print(f"[{time.strftime('%H:%M:%S')}] === INITIAL IMPORT Outlook ({OUTLOOK_EMAIL}) ===")
    imap = connect_imap()

    # Lister les dossiers disponibles
    folders = list_folders(imap)
    print(f"  Available folders: {folders}")

    # Microsoft 365 nomme ses dossiers differemment de Gmail :
    #   "Sent Items"    (pas "Sent Mail")
    #   "Deleted Items" (pas "Trash")
    #   "Junk Email"    (pas "Spam")
    # On scanne INBOX + Sent Items + tout dossier dont le nom evoque la reception.
    target_folders = []
    for f in folders:
        fl = f.lower()
        if any(k in fl for k in ["inbox", "sent items", "sent", "envoy"]):
            target_folders.append(f)

    # Toujours inclure INBOX en premier
    if "INBOX" not in target_folders:
        target_folders.insert(0, "INBOX")

    # Deduplication en preservant l'ordre
    seen = set()
    deduped = []
    for f in target_folders:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    target_folders = deduped

    print(f"  Folders to scan: {target_folders}")

    all_events = []
    for folder in target_folders:
        print(f"\n  --- Folder: {folder} ---")
        emails = fetch_emails_imap(imap, folder=folder, search_criteria="ALL")
        all_events.extend(emails)

    imap.logout()

    print(f"\n  Total: {len(all_events)} messages retrieved")
    if all_events:
        inserted = insert_events(all_events)
        print(f"  === {inserted} new events inserted (out of {len(all_events)} messages) ===")
    return len(all_events)


def poll_incremental():
    """Poll incremental : emails recus depuis 1 jour."""
    print(f"[{time.strftime('%H:%M:%S')}] Polling Outlook ({OUTLOOK_EMAIL})...")
    imap = connect_imap()

    since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
    search = f'(SINCE {since})'

    all_events = []
    for folder in ["INBOX"]:
        emails = fetch_emails_imap(imap, folder=folder, search_criteria=search)
        all_events.extend(emails)

    imap.logout()

    if not all_events:
        print("  No new messages")
        return 0

    inserted = insert_events(all_events)
    print(f"  {inserted} new events inserted")
    return inserted


def main():
    if not OUTLOOK_PASS:
        print("OUTLOOK_PASS missing. set OUTLOOK_PASS=...", file=sys.stderr)
        sys.exit(1)
    if not PG_PASS:
        print("PG_PASS missing. set PG_PASS=...", file=sys.stderr)
        sys.exit(1)

    try:
        import pg8000  # noqa
    except ImportError:
        print("ERROR: pip install pg8000", file=sys.stderr)
        sys.exit(1)

    if "--init" in sys.argv:
        poll_init()
    elif "--loop" in sys.argv:
        idx = sys.argv.index("--loop")
        interval = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 300
        print(f"Loop mode: polling every {interval}s. Ctrl+C to stop.")
        while True:
            poll_incremental()
            time.sleep(interval)
    else:
        poll_incremental()


if __name__ == "__main__":
    main()
