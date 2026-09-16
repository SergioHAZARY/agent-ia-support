# -*- coding: utf-8 -*-
"""
Polling Outlook shared mailbox → Supabase (table events).

Lit les emails de la boîte partagée Support-IT@francoisesaget.com
via Microsoft Graph API + device code flow (OAuth2).

Stratégie d'authentification :
  1. Essaie plusieurs client IDs publics Microsoft (certains tenants en
     bloquent certains mais pas d'autres)
  2. En fallback, utilise Outlook COM si Outlook est installé sur le poste

Prérequis : pip install msal requests pg8000

Au premier lancement, le script affiche un code et une URL.
Ouvrir l'URL dans un navigateur, entrer le code, et se connecter
avec SHAZARY_AA@atlasformen.com. Le token est ensuite mis en cache.

Mode --init   : import de TOUS les emails existants
Mode --loop N : poll continu toutes les N secondes (défaut 300)
Mode par défaut : un seul poll incrémental (emails reçus < 10 min)

Usage :
  set PG_PASS=...
  python scripts/poll_outlook.py --init
  python scripts/poll_outlook.py --loop 300
  python scripts/poll_outlook.py
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

# --- Configuration ---
OUTLOOK_EMAIL = os.environ.get("OUTLOOK_EMAIL", "SHAZARY_AA@atlasformen.com")
SHARED_MAILBOX = os.environ.get("SHARED_MAILBOX", "Support-IT@francoisesaget.com")

# Clients publics Microsoft à essayer dans l'ordre
# Chaque tenant peut bloquer certains clients mais pas d'autres
PUBLIC_CLIENTS = [
    ("14d82eec-204b-4c2f-b7e8-296a70dab67e", "Microsoft Graph PowerShell"),
    ("04b07795-8ddb-461a-bbdb-d681b202a308", "Azure CLI"),
    ("1950a258-227b-4e31-a9cf-717495945fc2", "Azure PowerShell"),
    ("d3590ed6-52b3-4102-aeff-aad2292ab01c", "Microsoft Office"),
]
AUTHORITY = "https://login.microsoftonline.com/organizations"
SCOPES = ["Mail.Read.Shared", "Mail.Read", "User.Read"]

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_CACHE_FILE = os.path.join(HERE, ".outlook_token_cache.json")
CLIENT_STATE_FILE = os.path.join(HERE, ".outlook_client_id.json")
PAGE_SIZE = 100
USE_COM = "--com" in sys.argv  # Forcer le mode Outlook COM


def get_graph_token():
    """Obtient un token Microsoft Graph via device code flow + cache.
    Essaie plusieurs client IDs publics Microsoft."""
    import msal

    # Charger le client ID qui a fonctionné la dernière fois
    saved_client = _load_saved_client()
    if saved_client:
        clients = [(saved_client["id"], saved_client["name"])] + \
                  [(c, n) for c, n in PUBLIC_CLIENTS if c != saved_client["id"]]
    else:
        clients = PUBLIC_CLIENTS

    for client_id, client_name in clients:
        print(f"  Essai avec {client_name} ({client_id[:8]}...)...")

        cache = msal.SerializableTokenCache()
        cache_file = TOKEN_CACHE_FILE.replace(".json", f"_{client_id[:8]}.json")
        if os.path.exists(cache_file):
            cache.deserialize(open(cache_file).read())

        app = msal.PublicClientApplication(
            client_id, authority=AUTHORITY, token_cache=cache
        )

        # Essai silencieux (token en cache)
        accounts = app.get_accounts(username=OUTLOOK_EMAIL)
        if accounts:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and "access_token" in result:
                print(f"  Token en cache valide ({client_name})")
                _save_cache(cache, cache_file)
                _save_client(client_id, client_name)
                return result["access_token"]

        # Device code flow
        try:
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                err = flow.get("error_description", str(flow))
                print(f"  {client_name}: device flow échoué — {err[:120]}")
                continue

            print(f"\n  === Authentification via {client_name} ===")
            print(f"\n  {flow['message']}\n")
            print("  En attente de l'authentification dans le navigateur...")

            result = app.acquire_token_by_device_flow(flow)
            if "access_token" in result:
                print(f"  Authentifié avec succès via {client_name} !")
                _save_cache(cache, cache_file)
                _save_client(client_id, client_name)
                return result["access_token"]

            err = result.get("error_description", result.get("error", ""))
            print(f"  {client_name}: auth échouée — {err[:150]}")

        except Exception as e:
            print(f"  {client_name}: erreur — {e}")

    print("\nERREUR: Aucun client public n'a fonctionné.", file=sys.stderr)
    print("Alternatives :", file=sys.stderr)
    print("  1. Demander à l'admin M365 d'autoriser une app", file=sys.stderr)
    print("  2. Utiliser le mode Outlook COM : python scripts/poll_outlook.py --com", file=sys.stderr)
    sys.exit(1)


def _save_cache(cache, cache_file):
    if cache.has_state_changed:
        with open(cache_file, "w") as f:
            f.write(cache.serialize())


def _load_saved_client():
    if os.path.exists(CLIENT_STATE_FILE):
        try:
            return json.load(open(CLIENT_STATE_FILE))
        except Exception:
            pass
    return None


def _save_client(client_id, client_name):
    with open(CLIENT_STATE_FILE, "w") as f:
        json.dump({"id": client_id, "name": client_name}, f)


def graph_get(token, url, params=None):
    """Appel GET à l'API Microsoft Graph."""
    import requests
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_emails_graph(token, since=None):
    """Récupère les emails de la boîte partagée via Graph API."""
    base_url = f"https://graph.microsoft.com/v1.0/users/{SHARED_MAILBOX}/mailFolders/inbox/messages"

    params = {
        "$select": "id,subject,from,receivedDateTime,body,internetMessageId",
        "$orderby": "receivedDateTime asc",
        "$top": PAGE_SIZE,
    }
    if since:
        params["$filter"] = f"receivedDateTime ge {since}"
        params["$orderby"] = "receivedDateTime desc"

    all_messages = []
    url = base_url
    page = 0

    while url:
        try:
            data = graph_get(token, url, params if page == 0 else None)
        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg or "Forbidden" in err_msg:
                print(f"  Accès refusé à {SHARED_MAILBOX}, essai via /me...")
                return _fetch_emails_me(token, since)
            raise

        messages = data.get("value", [])
        all_messages.extend(messages)
        page += 1
        print(f"  ... {len(all_messages)} emails chargés (page {page})")
        url = data.get("@odata.nextLink")

    return all_messages


def _fetch_emails_me(token, since=None):
    """Fallback : lit les mails du compte propre."""
    print(f"  Lecture de la boîte de {OUTLOOK_EMAIL}...")
    base_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
    params = {
        "$select": "id,subject,from,receivedDateTime,body,internetMessageId",
        "$orderby": "receivedDateTime asc",
        "$top": PAGE_SIZE,
    }
    if since:
        params["$filter"] = f"receivedDateTime ge {since}"
        params["$orderby"] = "receivedDateTime desc"

    all_messages = []
    url = base_url
    page = 0
    while url:
        data = graph_get(token, url, params if page == 0 else None)
        messages = data.get("value", [])
        all_messages.extend(messages)
        page += 1
        print(f"  ... {len(all_messages)} emails chargés (page {page})")
        url = data.get("@odata.nextLink")
    return all_messages


# ============================================================
# Mode Outlook COM — fallback Windows quand Graph est bloqué
# ============================================================

def fetch_emails_com(since=None):
    """Lit les emails via Outlook COM (Windows + Outlook installé)."""
    import win32com.client

    outlook = win32com.client.Dispatch("Outlook.Application")
    mapi = outlook.GetNamespace("MAPI")

    # Chercher la boîte partagée dans les stores
    shared_store = None
    for store in mapi.Stores:
        if SHARED_MAILBOX.lower() in store.DisplayName.lower():
            shared_store = store
            break

    if shared_store:
        inbox = shared_store.GetDefaultFolder(6)  # 6 = olFolderInbox
        print(f"  Boîte partagée trouvée: {shared_store.DisplayName}")
    else:
        # Essayer via les destinataires
        try:
            recip = mapi.CreateRecipient(SHARED_MAILBOX)
            recip.Resolve()
            if recip.Resolved:
                inbox = mapi.GetSharedDefaultFolder(recip, 6)
                print(f"  Accès via GetSharedDefaultFolder: {SHARED_MAILBOX}")
            else:
                print(f"  Boîte partagée non trouvée, utilisation de la boîte par défaut")
                inbox = mapi.GetDefaultFolder(6)
        except Exception as e:
            print(f"  Erreur accès partagé: {e}")
            inbox = mapi.GetDefaultFolder(6)

    items = inbox.Items
    items.Sort("[ReceivedTime]", False)  # Plus ancien d'abord

    if since:
        since_str = since.strftime("%m/%d/%Y %H:%M")
        items = items.Restrict(f"[ReceivedTime] >= '{since_str}'")

    messages = []
    count = 0
    for item in items:
        try:
            msg = {
                "id": getattr(item, "EntryID", ""),
                "subject": getattr(item, "Subject", "(sans objet)"),
                "from": {"emailAddress": {
                    "name": getattr(item, "SenderName", ""),
                    "address": getattr(item, "SenderEmailAddress", ""),
                }},
                "receivedDateTime": str(getattr(item, "ReceivedTime", "")),
                "body": {"content": getattr(item, "Body", ""), "contentType": "text"},
                "internetMessageId": getattr(item, "InternetMessageID",
                                             getattr(item, "EntryID", "")),
            }
            messages.append(msg)
            count += 1
            if count % 100 == 0:
                print(f"  ... {count} emails lus")
        except Exception as e:
            print(f"  ! Erreur lecture COM: {e}", file=sys.stderr)

    print(f"  {len(messages)} emails lus via Outlook COM")
    return messages


# ============================================================
# Normalisation et insertion
# ============================================================

def normalize_email(msg):
    """Normalise un message (Graph ou COM) en event Supabase."""
    sender = ""
    from_field = msg.get("from", {}).get("emailAddress", {})
    sender = from_field.get("name") or from_field.get("address", "")

    subject = msg.get("subject", "(sans objet)")

    body_obj = msg.get("body", {})
    body_text = body_obj.get("content", "")
    if body_obj.get("contentType") == "html":
        body_text = re.sub(r"<[^>]+>", " ", body_text)
        body_text = re.sub(r"&nbsp;", " ", body_text)
    body_text = re.sub(r"\s+", " ", body_text).strip()

    body = f"[Email] {subject}"
    if body_text:
        body += f" — {body_text}"

    msg_id = msg.get("internetMessageId") or msg.get("id", "")

    return {
        "platform": "email",
        "external_id": SHARED_MAILBOX.lower(),
        "source_message_id": msg_id[:255],
        "sender_raw": sender[:255],
        "body": body[:2000],
    }


def get_pg_conn():
    import pg8000
    return pg8000.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DB,
        user=PG_USER, password=PG_PASS, ssl_context=True
    )


def insert_events(events):
    conn = get_pg_conn()
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
                print(f"  + {ev['sender_raw'][:20]:20s} {ev['body'][:70]}")
        except Exception as e:
            print(f"  ! {ev['source_message_id'][:30]}: {e}", file=sys.stderr)
    conn.close()
    return inserted


def poll_init(fetch_fn):
    """Import initial : TOUS les emails."""
    print(f"[{time.strftime('%H:%M:%S')}] === IMPORT INITIAL Outlook ({SHARED_MAILBOX}) ===")
    messages = fetch_fn()
    print(f"  Total: {len(messages)} emails récupérés")
    events = [normalize_email(m) for m in messages]
    total_inserted = 0
    for i in range(0, len(events), PAGE_SIZE):
        batch = events[i:i + PAGE_SIZE]
        inserted = insert_events(batch)
        total_inserted += inserted
    print(f"  === {total_inserted} nouveaux events insérés (sur {len(events)} emails) ===")
    return total_inserted


def poll_incremental(fetch_fn):
    """Poll incrémental : emails reçus dans les 10 dernières minutes."""
    print(f"[{time.strftime('%H:%M:%S')}] Polling Outlook ({SHARED_MAILBOX})...")
    since = datetime.now(timezone.utc) - timedelta(minutes=10)

    try:
        messages = fetch_fn(since=since)
    except Exception as e:
        print(f"  Erreur: {e}", file=sys.stderr)
        return 0

    if not messages:
        print("  Aucun nouvel email")
        return 0

    events = [normalize_email(m) for m in messages]
    inserted = insert_events(events)
    print(f"  {inserted} nouveaux events insérés (sur {len(events)} emails)")
    return inserted


def main():
    if not PG_PASS:
        print("PG_PASS manquant. set PG_PASS=...", file=sys.stderr)
        sys.exit(1)

    # Choisir le mode de connexion
    if USE_COM:
        print("Mode Outlook COM (lecture directe via Outlook installé)")
        try:
            import win32com.client  # noqa
        except ImportError:
            print("ERREUR: pip install pywin32", file=sys.stderr)
            sys.exit(1)

        def fetch_fn(since=None):
            return fetch_emails_com(since=since)
    else:
        print("Mode Microsoft Graph (OAuth device code flow)")
        for lib in ("msal", "requests", "pg8000"):
            try:
                __import__(lib)
            except ImportError:
                print(f"ERREUR: pip install {lib}", file=sys.stderr)
                sys.exit(1)

        print(f"Authentification pour {SHARED_MAILBOX}...")
        token = get_graph_token()

        def fetch_fn(since=None):
            since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None
            return fetch_emails_graph(token, since=since_str)

    if "--init" in sys.argv:
        poll_init(fetch_fn)
    elif "--loop" in sys.argv:
        idx = sys.argv.index("--loop")
        interval = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 300
        print(f"Mode boucle : poll toutes les {interval}s. Ctrl+C pour arrêter.")
        while True:
            if not USE_COM:
                t = get_graph_token()
                def _fetch(since=None, _t=t):
                    s = since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None
                    return fetch_emails_graph(_t, since=s)
                poll_incremental(_fetch)
            else:
                poll_incremental(fetch_fn)
            time.sleep(interval)
    else:
        poll_incremental(fetch_fn)


if __name__ == "__main__":
    main()
