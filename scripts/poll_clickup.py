# -*- coding: utf-8 -*-
"""
Polling ClickUp → Supabase (table events).

Récupère TOUTES les tâches ClickUp du workspace Regard Beauty avec :
- Assignee, status (Backlog, Open, En Cours, Pending, Review, Validated, Closed)
- Tags/labels (bazarchic, paul beuscher, smallable, etc.)
- Département déduit du tag ou du space/folder

Prérequis : pip install pg8000

Pour obtenir le token API ClickUp :
  1. Aller sur https://app.clickup.com/settings/apps
  2. "Generate" un Personal API Token
  3. Le passer en variable CLICKUP_TOKEN

Mode --init   : import de TOUTES les tâches
Mode --loop N : poll continu toutes les N secondes (défaut 300)
Mode par défaut : un seul poll incrémental (tâches modifiées < 10 min)

Usage :
  set CLICKUP_TOKEN=pk_...
  set PG_PASS=...
  python scripts/poll_clickup.py --init
  python scripts/poll_clickup.py --loop 300
  python scripts/poll_clickup.py
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

# --- Configuration ---
CLICKUP_TOKEN = os.environ.get("CLICKUP_TOKEN", "")
TEAM_ID = os.environ.get("CLICKUP_TEAM_ID", "9012970281")

PG_HOST = os.environ.get("PG_HOST", "aws-1-eu-west-1.pooler.supabase.com")
PG_PORT = int(os.environ.get("PG_PORT", "6543"))
PG_DB = os.environ.get("PG_DB", "postgres")
PG_USER = os.environ.get("PG_USER", "postgres.ztmzhvzjfsncwuvzbhua")
PG_PASS = os.environ.get("PG_PASS", "")

BATCH_SIZE = 200


def clickup_get(path, params=None):
    """Appel GET à l'API ClickUp v2."""
    url = f"https://api.clickup.com/api/v2{path}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(url)
    req.add_header("Authorization", CLICKUP_TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get_pg_conn():
    import pg8000
    return pg8000.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DB,
        user=PG_USER, password=PG_PASS, ssl_context=True
    )


def get_spaces():
    """Récupère tous les spaces du workspace."""
    data = clickup_get(f"/team/{TEAM_ID}/space", {"archived": "false"})
    return data.get("spaces", [])


def get_folders(space_id):
    """Récupère tous les folders d'un space."""
    data = clickup_get(f"/space/{space_id}/folder", {"archived": "false"})
    return data.get("folders", [])


def get_lists_in_folder(folder_id):
    """Récupère toutes les lists d'un folder."""
    data = clickup_get(f"/folder/{folder_id}/list", {"archived": "false"})
    return data.get("lists", [])


def get_folderless_lists(space_id):
    """Récupère les lists sans folder dans un space."""
    data = clickup_get(f"/space/{space_id}/list", {"archived": "false"})
    return data.get("lists", [])


def get_tasks(list_id, page=0, include_closed=True):
    """Récupère les tâches d'une list avec pagination."""
    params = {
        "page": page,
        "include_closed": "true" if include_closed else "false",
        "subtasks": "true",
    }
    data = clickup_get(f"/list/{list_id}/task", params)
    return data.get("tasks", []), data.get("last_page", True)


def normalize_task(task, space_name="", folder_name="", list_name=""):
    """Normalise une tâche ClickUp en event."""
    status = (task.get("status") or {}).get("status", "unknown")
    assignees = [a.get("username") or a.get("email", "") for a in task.get("assignees", [])]
    assignees_str = ", ".join(assignees) if assignees else "non assigné"
    tags = [t.get("name", "") for t in task.get("tags", [])]
    tags_str = ", ".join(tags) if tags else ""
    priority = (task.get("priority") or {}).get("priority", "none")

    # Déterminer le département via les tags
    department = "regardbeauty"  # défaut
    tag_lower = [t.lower() for t in tags]
    if "bazarchic" in tag_lower:
        department = "bazarchic"
    elif "paul beuscher" in tag_lower or "paulbeuscher" in tag_lower:
        department = "paulbeuscher"
    elif "smallable" in tag_lower:
        department = "smallable"

    name = task.get("name", "")
    desc = (task.get("description") or "")[:500]

    body = f"[{space_name}/{folder_name}/{list_name}] [{status}] [{priority}] {name}"
    if assignees_str:
        body += f" | Assigné: {assignees_str}"
    if tags_str:
        body += f" | Tags: {tags_str}"
    if desc:
        body += f" — {desc}"

    return {
        "platform": "clickup",
        "external_id": TEAM_ID,
        "source_message_id": task.get("id", ""),
        "sender_raw": assignees_str,
        "body": body[:2000],
        "department": department,
    }


def fetch_all_tasks():
    """Récupère toutes les tâches de tous les spaces/folders/lists."""
    all_tasks = []
    spaces = get_spaces()
    print(f"  {len(spaces)} space(s) trouves")

    for space in spaces:
        space_name = space.get("name", "")
        space_id = space.get("id", "")
        print(f"\n  === Space: {space_name} (id={space_id}) ===")

        # Folders
        folders = get_folders(space_id)
        for folder in folders:
            folder_name = folder.get("name", "")
            folder_id = folder.get("id", "")
            print(f"    Folder: {folder_name}")

            lists = get_lists_in_folder(folder_id)
            for lst in lists:
                list_name = lst.get("name", "")
                list_id = lst.get("id", "")
                page = 0
                while True:
                    tasks, last_page = get_tasks(list_id, page=page)
                    for t in tasks:
                        all_tasks.append(normalize_task(t, space_name, folder_name, list_name))
                    if not tasks or last_page:
                        break
                    page += 1
                if tasks:
                    print(f"      List: {list_name} -> {len(tasks)} tache(s)")

        # Folderless lists
        fl_lists = get_folderless_lists(space_id)
        for lst in fl_lists:
            list_name = lst.get("name", "")
            list_id = lst.get("id", "")
            page = 0
            list_count = 0
            while True:
                tasks, last_page = get_tasks(list_id, page=page)
                for t in tasks:
                    all_tasks.append(normalize_task(t, space_name, "", list_name))
                list_count += len(tasks)
                if not tasks or last_page:
                    break
                page += 1
            if list_count:
                print(f"    List (sans folder): {list_name} -> {list_count} tache(s)")

    print(f"\n  Total: {len(all_tasks)} taches recuperees")
    return all_tasks


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


def poll_init():
    """Import initial : TOUTES les tâches."""
    print(f"[{time.strftime('%H:%M:%S')}] === IMPORT INITIAL ClickUp (team {TEAM_ID}) ===")
    tasks = fetch_all_tasks()
    if tasks:
        inserted = insert_events(tasks)
        print(f"  === {inserted} nouveaux events inseres (sur {len(tasks)} taches) ===")
    return len(tasks)


def poll_incremental():
    """Poll incrémental : tâches modifiées récemment."""
    print(f"[{time.strftime('%H:%M:%S')}] Polling ClickUp (team {TEAM_ID})...")
    # ClickUp ne filtre pas facilement par date dans l'API lists/tasks,
    # on fait un scan complet et le ON CONFLICT dédoublonne
    tasks = fetch_all_tasks()
    if not tasks:
        print("  Aucune tache trouvee")
        return 0
    inserted = insert_events(tasks)
    print(f"  {inserted} nouveaux events inseres")
    return inserted


def main():
    if not CLICKUP_TOKEN:
        print("CLICKUP_TOKEN manquant.", file=sys.stderr)
        print("Pour obtenir le token :", file=sys.stderr)
        print("  1. Aller sur https://app.clickup.com/settings/apps", file=sys.stderr)
        print("  2. Cliquer 'Generate' pour creer un Personal API Token", file=sys.stderr)
        print("  3. set CLICKUP_TOKEN=pk_...", file=sys.stderr)
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
        print(f"Mode boucle : poll toutes les {interval}s. Ctrl+C pour arreter.")
        while True:
            poll_incremental()
            time.sleep(interval)
    else:
        poll_incremental()


if __name__ == "__main__":
    main()
