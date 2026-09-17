# -*- coding: utf-8 -*-
"""
Polling Gmail (IMAP) → Supabase (table events).

Récupère les emails et messages Google Chat de itsupport@bazarchic.com
via IMAP (les messages Google Chat apparaissent dans Gmail quand le
paramètre est activé).

Prérequis : pip install pg8000

IMPORTANT : Si le compte a la 2FA activée, il faut un mot de passe
d'application (App Password) au lieu du mot de passe normal.
Sinon, activer "Accès aux applications moins sécurisées" dans les
paramètres Google (déconseillé) ou utiliser un App Password.

Mode --init   : import de TOUS les emails existants
Mode --loop N : poll continu toutes les N secondes (défaut 300)
Mode par défaut : un seul poll incrémental (emails reçus < 1 jour)

Usage :
  set GMAIL_PASS=...
  set PG_PASS=...
  python scripts/poll_gmail.py --init
  python scripts/poll_gmail.py --loop 300
  python scripts/poll_gmail.py
"""
import email
import email.header
import email.utils
import imaplib
import os
import re
import sys
import time
from datetime import datetime, timedelta

# --- Configuration ---
GMAIL_EMAIL = os.environ.get("GMAIL_EMAIL", "itsupport@bazarchic.com")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

DEPARTMENT = os.environ.get("DEPARTMENT", "bazarchic")
BATCH_SIZE = 200


def get_pg_conn():
    import pg8000
    return pg8000.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DB,
        user=PG_USER, password=PG_PASS, ssl_context=True
    )


def decode_header_value(raw):
    """Décode un en-tête MIME (RFC 2047)."""
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
    """Récupère les emails depuis un dossier IMAP."""
    emails = []
    try:
        status, _ = imap.select(folder, readonly=True)
        if status != "OK":
            print(f"  ! Impossible d'ouvrir {folder}", file=sys.stderr)
            return emails
    except Exception as e:
        print(f"  ! Erreur select {folder}: {e}", file=sys.stderr)
        return emails

    status, data = imap.search(None, search_criteria)
    if status != "OK":
        return emails

    msg_ids = data[0].split()
    if not msg_ids:
        return emails

    print(f"  {folder}: {len(msg_ids)} messages trouvés")
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
            message_id = msg.get("Message-ID", f"gmail-{folder}-{mid.decode()}")
            date_str = msg.get("Date", "")

            body = extract_body(msg)
            if not body:
                body = subject

            # Nettoyer le message_id
            message_id = message_id.strip().strip("<>")
            if not message_id:
                message_id = f"gmail-{folder}-{mid.decode()}"

            emails.append({
                "platform": "email",
                "external_id": GMAIL_EMAIL,
                "source_message_id": message_id[:500],
                "sender_raw": from_addr[:500],
                "body": f"[{subject}] {body}"[:2000],
            })

            if (i + 1) % 100 == 0:
                print(f"  ... {i + 1}/{len(msg_ids)} messages lus")
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


def connect_imap():
    """Connexion IMAP avec SSL."""
    print(f"  Connexion IMAP à {IMAP_SERVER}:{IMAP_PORT} en tant que {GMAIL_EMAIL}...")
    imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    imap.login(GMAIL_EMAIL, GMAIL_PASS)
    print("  Connecté.")
    return imap


def insert_events(events):
    """Insère les events dans Supabase avec reconnexion par batch."""
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
    print(f"[{time.strftime('%H:%M:%S')}] === IMPORT INITIAL Gmail ({GMAIL_EMAIL}) ===")
    imap = connect_imap()

    # Lister les dossiers
    folders = list_folders(imap)
    print(f"  Dossiers disponibles: {folders}")

    # Dossiers à scanner (INBOX + Chat + envoyés)
    # Google Chat apparaît dans le label "Chats" ou "[Gmail]/Chats"
    target_folders = []
    for f in folders:
        fl = f.lower()
        if any(k in fl for k in ["inbox", "chat", "sent", "envoy", "all mail", "tous"]):
            target_folders.append(f)

    # Toujours inclure INBOX
    if "INBOX" not in target_folders:
        target_folders.insert(0, "INBOX")

    print(f"  Dossiers à scanner: {target_folders}")

    all_events = []
    for folder in target_folders:
        print(f"\n  --- Dossier: {folder} ---")
        emails = fetch_emails_imap(imap, folder=folder, search_criteria="ALL")
        all_events.extend(emails)

    imap.logout()

    print(f"\n  Total: {len(all_events)} messages récupérés")
    if all_events:
        inserted = insert_events(all_events)
        print(f"  === {inserted} nouveaux events insérés (sur {len(all_events)} messages) ===")
    return len(all_events)


def poll_incremental():
    """Poll incrémental : emails reçus depuis 1 jour."""
    print(f"[{time.strftime('%H:%M:%S')}] Polling Gmail ({GMAIL_EMAIL})...")
    imap = connect_imap()

    since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
    search = f'(SINCE {since})'

    all_events = []
    for folder in ["INBOX"]:
        emails = fetch_emails_imap(imap, folder=folder, search_criteria=search)
        all_events.extend(emails)

    imap.logout()

    if not all_events:
        print("  Aucun nouveau message")
        return 0

    inserted = insert_events(all_events)
    print(f"  {inserted} nouveaux events insérés")
    return inserted


def main():
    if not GMAIL_PASS:
        print("GMAIL_PASS manquant. set GMAIL_PASS=...", file=sys.stderr)
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
