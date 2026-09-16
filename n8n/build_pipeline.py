# -*- coding: utf-8 -*-
"""
Construit LE workflow unique 'AgentSupport - Pipeline' -> n8n/workflows/agentsupport-pipeline.json

Multicanal (Telegram, Teams/Google Chat/ClickUp/Jira via webhook, email IMAP,
manuel), Agent Claude natif n8n (lmChatAnthropic -> chainLlm), garde-fous en
code, PostgreSQL pour events/tickets, et une branche reporting.

Reproductible : la logique des garde-fous vient de n8n/guardrails.js, le prompt
de prompts/triage.md. Regenerer apres modification :  python n8n/build_pipeline.py
Deploiement : n8n/sync.py (et l'Action GitHub deploy-n8n.yml).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GUARDRAILS_JS = open(os.path.join(HERE, "guardrails.js"), encoding="utf-8").read()
GUARDRAILS_JS = GUARDRAILS_JS.replace(
    "module.exports = { applyGuardrails, checkForbidden };", "").rstrip()

# Références de credentials (id + nom), non secrètes. Absent -> pas de creds.
CREDS = {}
_cp = os.path.join(HERE, "credentials.json")
if os.path.exists(_cp):
    CREDS = {k: v for k, v in json.load(open(_cp, encoding="utf-8")).items()
             if not k.startswith("_")}


def system_prompt():
    t = open(os.path.join(ROOT, "prompts", "triage.md"), encoding="utf-8").read()
    s = t.index("```text") + len("```text")
    e = t.index("```", s)
    return t[s:e].strip()


def node(name, ntype, ver, pos, params=None, creds=None, disabled=False):
    n = {"name": name, "type": ntype, "typeVersion": ver, "position": pos,
         "parameters": params or {}}
    if creds:
        n["credentials"] = creds
    if disabled:
        n["disabled"] = True
    return n


def sticky(name, content, pos, w=460, h=220):
    return {"name": name, "type": "n8n-nodes-base.stickyNote", "typeVersion": 1,
            "position": pos, "parameters": {"content": content, "height": h, "width": w}}


def conn(pairs):
    """pairs: (src, dst, out_index, type). type defaut 'main'."""
    c = {}
    for p in pairs:
        src, dst = p[0], p[1]
        out_idx = p[2] if len(p) > 2 else 0
        ctype = p[3] if len(p) > 3 else "main"
        c.setdefault(src, {})
        c[src].setdefault(ctype, [])
        while len(c[src][ctype]) <= out_idx:
            c[src][ctype].append([])
        c[src][ctype][out_idx].append({"node": dst, "type": ctype, "index": 0})
    return c


def merge_conn(*cs):
    out = {}
    for c in cs:
        for src, types in c.items():
            out.setdefault(src, {})
            for t, arr in types.items():
                out[src].setdefault(t, [])
                while len(out[src][t]) < len(arr):
                    out[src][t].append([])
                for i, grp in enumerate(arr):
                    out[src][t][i].extend(grp)
    return out


# --------------------------------------------------------------------------- #
# Code des noeuds
# --------------------------------------------------------------------------- #
NORMALIZE_JS = r"""
// Normalisation MULTICANAL vers un evenement unique.
// Detecte la source d'apres la forme de l'entree.
const j = $json;
let ev;

if (j.message || j.channel_post) {                 // Telegram
  const t = j.message || j.channel_post;
  ev = { platform:'telegram', external_id:String((t.chat||{}).id||''),
    source_message_id:String(t.message_id||''),
    sender_external_id:String((t.from||{}).id||''),
    sender_raw:[(t.from||{}).first_name,(t.from||{}).last_name,(t.from||{}).username].filter(Boolean).join(' '),
    message_body: t.text || t.caption || (t.photo ? '[photo]' : '') || (t.document ? '[fichier]' : '') || (t.voice ? '[vocal]' : '') || (t.sticker ? '[sticker]' : '') || '' };
} else if (j.headers && j.body) {                  // Webhook (Teams/GChat/ClickUp/Jira)
  const b = j.body || {};
  // 'platform' peut etre passe en query (?platform=teams) ou devine
  const plat = (j.query && j.query.platform) || b.platform || 'webhook';
  ev = { platform: plat,
    external_id: String(b.channel_id || b.space || (b.space||{}).name || b.chat_id || b.list_id || ''),
    source_message_id: String(b.message_id || b.eventId || (b.message||{}).name || Date.now()),
    sender_external_id: String(b.user_id || (b.from||{}).id || (b.user||{}).name || ''),
    sender_raw: String(b.user_name || (b.from||{}).user || (b.user||{}).displayName || ''),
    message_body: b.text || (b.message||{}).text || b.description || b.content || '' };
} else if (j.textPlain || j.textHtml || j.subject) {  // Email IMAP
  ev = { platform:'email',
    external_id: String(((j.from||{}).value||[{}])[0].address || j.from || ''),
    source_message_id: String(j.messageId || j.uid || Date.now()),
    sender_external_id: String(((j.from||{}).value||[{}])[0].address || ''),
    sender_raw: String(((j.from||{}).value||[{}])[0].name || j.from || ''),
    message_body: (j.subject ? j.subject + ' — ' : '') + (j.textPlain || '') };
} else {                                            // Manuel (test)
  ev = { platform:'telegram', external_id:'demo', source_message_id:String(Date.now()),
    sender_external_id:'0', sender_raw:'Demo (non verifie)',
    message_body: j.message_body || "Bonjour, pouvez-vous m'ajouter a l'outil de design ?" };
}
ev.received_at = new Date().toISOString();
return [{ json: ev }];
""".strip()

ASSEMBLE_JS = r"""
// WF-02 : contexte. Fusionne ce que PostgreSQL a renvoye (societe/policies)
// avec des defauts prudents. Si le canal est inconnu -> palier P1.
const pg = $json && $json.tenant_id ? $json : null;   // société résolue, ou null (canal inconnu)

const ctx = {
  max_autonomy: (pg && pg.max_autonomy) || 'N3',
  observation_only: pg ? !!pg.observation_only : false,
  identity: { verified: false, role: 'employe', email: null },  // WF-09 remplira
  runbooks: {},                                                 // v_runbooks_disponibles
  taxonomy_categories: ['acces_licences','compte_identite','messagerie','poste_travail',
    'reseau','applicatif','developpement','securite','information','hors_perimetre'],
  confidence_floor: 0.7,
};

// La société résolue (si le canal est déclaré) est reportée sur l'événement
// pour lier le ticket. Sinon null : l'agent trie et trace quand même (P1).
const src = Object.assign({}, $('Normaliser (multicanal)').item.json,
  { tenant_id: (pg && pg.tenant_id) || null });

const userContent =
  "<demandeur>" + (src.sender_raw || 'INCONNU') + "</demandeur>\n" +
  "<canal>" + src.platform + " / " + (src.external_id||'') + "</canal>\n" +
  "<historique></historique>\n<kb></kb>\n<cas_similaires></cas_similaires>\n" +
  "<runbooks></runbooks>\n\n<message>\n" + (src.message_body||'') + "\n</message>";

// L'Agent Claude (chainLlm) recoit prompt systeme + contexte en un seul texte.
return [{ json: { ctx, userContent, source: src,
  llm_prompt: (SYSTEM_PROMPT_PLACEHOLDER) + "\n\n" + userContent } }];
""".strip()

PARSE_GUARD_JS = GUARDRAILS_JS + r"""

// Recupere la sortie de l'Agent Claude (chainLlm : champ text/output), parse, garde-fous.
const ctx = $('Assembler le contexte').item.json.ctx;
const src = $('Assembler le contexte').item.json.source;
let modelText = $json.text || $json.output || '';
if (!modelText && Array.isArray($json.content)) modelText = $json.content.map(b=>b.text||'').join('');
let raw;
try { raw = JSON.parse(modelText); }
catch (e) {
  raw = { is_ticket:true, title:'triage illisible', summary:String(modelText).slice(0,200),
          category:'information', subcategory:'question_procedure', priority:'p3',
          autonomy_level:'N3', confidence:0, alert:'reponse du modele non parsable' };
}
const r = applyGuardrails(raw, ctx);
return [{ json: Object.assign({}, r.triage, {
  _adjustments:r.adjustments, _alerts:r.alerts, _source:src }) }];
"""

REPORT_JS = r"""
// Formate la reponse de la requete reporting pour le webhook.
const rows = $input.all().map(i => i.json);
return [{ json: { count: rows.length, tickets: rows } }];
""".strip()

SYSTEM = system_prompt()
ASSEMBLE_JS = ASSEMBLE_JS.replace(
    "(SYSTEM_PROMPT_PLACEHOLDER)", json.dumps(SYSTEM, ensure_ascii=False))

# ----- PostgreSQL (executeQuery, requetes parametrees) -----------------------
# Pas de credential codee : a attacher dans l'UI (credential 'Postgres AgentSupport').

# Trace TOUJOURS l'evenement, meme si le canal n'est pas declare (channel_id null).
# CTE + select final => renvoie toujours exactement une ligne (le flux continue).
PG_EVENT_SQL = (
    "with ins as ("
    " insert into events (channel_id, tenant_id, source_message_id, sender_raw, body, received_at)"
    " select c.id, c.tenant_id, $1, $2, $3, now()"
    " from (select $4::text as platform, $5::text as external_id) q"
    " left join channels c on c.platform = q.platform and c.external_id = q.external_id"
    " on conflict (channel_id, source_message_id) do nothing"
    " returning id"
    ") select (select id from ins) as event_id;")

# Resout la societe SI le canal est actif ; sinon renvoie une ligne a null
# (grace au select-from-dummy en LEFT JOIN) => le flux ne s'interrompt jamais.
PG_CTX_SQL = (
    "select c.tenant_id, t.max_autonomy, t.observation_only "
    "from (select $1::text as platform, $2::text as external_id) q "
    "left join channels c on c.platform = q.platform and c.external_id = q.external_id "
    "and c.status = 'active' "
    "left join tenants t on t.id = c.tenant_id;")

PG_TICKET_SQL = (
    "insert into tickets (tenant_id, title, summary, category, subcategory, "
    "priority, autonomy_level, confidence, status, ai_response, escalation_reason) "
    "values ($1,$2,$3,$4,$5,$6,$7,$8,'nouveau',$9,$10) returning id, ref;")

PG_REPORT_SQL = "select * from v_tickets_ouverts limit 100;"


def pg_node(name, pos, sql, repl_expr):
    return node(name, "n8n-nodes-base.postgres", 2.6, pos, {
        "operation": "executeQuery",
        "query": sql,
        "options": {"queryReplacement": repl_expr},
    }, creds=({"postgres": CREDS["postgres"]} if "postgres" in CREDS else None))


# --------------------------------------------------------------------------- #
# Noeuds
# --------------------------------------------------------------------------- #
NOTE = (
    "## AgentSupport - Pipeline (workflow unique, multicanal)\n\n"
    "Canaux -> n8n normalise -> PostgreSQL (events) -> contexte -> AGENT CLAUDE\n"
    "-> GARDE-FOUS (regle 5) -> ticket -> route N0/N1/N2/N3.\n"
    "Branche reporting separee : webhook -> PostgreSQL -> reponse.\n\n"
    "Genere depuis git (n8n/build_pipeline.py). Ne pas editer a la main.\n\n"
    "CREDENTIALS A ATTACHER (puis activer) :\n"
    "- Postgres 'Postgres AgentSupport' (Supabase ou VPS)\n"
    "- Anthropic (noeud Agent Claude)\n"
    "- Telegram / Teams / Google Chat / IMAP selon les canaux ouverts"
)

nodes = [
    sticky("note", NOTE, [-60, -320], w=680, h=320),

    # --- Declencheurs multicanal ---
    node("TelegramTrigger", "n8n-nodes-base.telegramTrigger", 1.2, [-60, 60],
         {"updates": ["message"], "additionalFields": {}},
         creds=({"telegramApi": CREDS["telegramApi"]} if "telegramApi" in CREDS else None)),
    node("Webhook multicanal", "n8n-nodes-base.webhook", 1.1, [-60, 220], {
        "httpMethod": "POST", "path": "agent-support",
        "responseMode": "onReceived", "options": {}}),
    # Desactive : aucun compte itsupport@ n'a encore de credential IMAP dans
    # n8n. Un noeud trigger sans credential bloque l'activation du workflow
    # entier (verifie via l'API : "Node does not have any credentials set").
    # Reactiver (disabled=False) une fois la credential IMAP creee dans n8n.
    node("Email IMAP", "n8n-nodes-base.emailReadImap", 2, [-60, 380], {"options": {}},
         disabled=True),
    node("Declencheur manuel", "n8n-nodes-base.manualTrigger", 1, [-60, 520]),

    # --- Pipeline principal ---
    node("Normaliser (multicanal)", "n8n-nodes-base.code", 2, [220, 240],
         {"jsCode": NORMALIZE_JS}),
    pg_node("PG: enregistrer evenement", [440, 240], PG_EVENT_SQL,
            "={{ [$json.source_message_id, $json.sender_raw, $json.message_body, "
            "$json.platform, $json.external_id] }}"),
    pg_node("PG: resoudre societe", [660, 240], PG_CTX_SQL,
            "={{ [$('Normaliser (multicanal)').item.json.platform, "
            "$('Normaliser (multicanal)').item.json.external_id] }}"),
    node("Assembler le contexte", "n8n-nodes-base.code", 2, [880, 240],
         {"jsCode": ASSEMBLE_JS}),

    # --- Agent Claude natif ---
    node("Agent Claude (triage)", "@n8n/n8n-nodes-langchain.chainLlm", 1.5, [1120, 240],
         {"promptType": "define", "text": "={{ $json.llm_prompt }}"}),
    node("Modele Anthropic", "@n8n/n8n-nodes-langchain.lmChatAnthropic", 1.3, [1120, 460], {
        "model": {"__rl": True, "mode": "id",
                  "value": "claude-sonnet-4-20250514"},
        "options": {"maxTokensToSample": 2000, "temperature": 0}},
        creds=({"anthropicApi": CREDS["anthropicApi"]} if "anthropicApi" in CREDS else None)),

    node("Parser + garde-fous", "n8n-nodes-base.code", 2, [1360, 240],
         {"jsCode": PARSE_GUARD_JS}),
    node("Est-ce un ticket ?", "n8n-nodes-base.if", 2.3, [1580, 240], {
        "conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                       "combinator": "and",
                       "conditions": [{"id": "c1",
                                       "leftValue": "={{ $json.is_ticket }}",
                                       "rightValue": "",
                                       "operator": {"type": "boolean", "operation": "true",
                                                    "singleValue": True}}]},
        "options": {}}),
    node("Ignore (pas un ticket)", "n8n-nodes-base.noOp", 1, [1800, 40]),
    pg_node("PG: creer le ticket", [1800, 300], PG_TICKET_SQL,
            "={{ [$json._source.tenant_id || null, $json.title, $json.summary, "
            "$json.category, $json.subcategory, $json.priority, "
            "$json.autonomy_level, $json.confidence, "
            "$json.proposed_response || '', $json.escalation_reason || ''] }}"),
    node("Router par niveau", "n8n-nodes-base.switch", 3.2, [2020, 300], {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                            "conditions": [{"leftValue": "={{ $('Parser + garde-fous').item.json.autonomy_level }}",
                                            "rightValue": lvl,
                                            "operator": {"type": "string", "operation": "equals"}}]},
             "outputKey": lvl} for lvl in ["N0", "N1", "N2", "N3"]]},
        "options": {}}),
    node("N0 repondre", "n8n-nodes-base.noOp", 1, [2260, 120]),
    node("N1 valider puis executer", "n8n-nodes-base.noOp", 1, [2260, 260]),
    node("N2 guider", "n8n-nodes-base.noOp", 1, [2260, 400]),
    node("N3 escalader", "n8n-nodes-base.noOp", 1, [2260, 540]),

    # --- Branche reporting ---
    sticky("noteReport",
           "## Reporting\nPOST /webhook/agent-support-reporting\n"
           "Interroge la base unique (v_tickets_ouverts) : tickets en cours toutes\n"
           "sources confondues. C'est PostgreSQL qui rend le reporting possible.",
           [-60, 760], w=520, h=160),
    node("Webhook reporting", "n8n-nodes-base.webhook", 1.1, [220, 820], {
        "httpMethod": "GET", "path": "agent-support-reporting",
        "responseMode": "lastNode", "options": {}}),
    pg_node("PG: tickets en cours", [440, 820], PG_REPORT_SQL, ""),
    node("Formater le rapport", "n8n-nodes-base.code", 2, [660, 820], {"jsCode": REPORT_JS}),
]

connections = merge_conn(
    conn([
        ("TelegramTrigger", "Normaliser (multicanal)"),
        ("Webhook multicanal", "Normaliser (multicanal)"),
        ("Email IMAP", "Normaliser (multicanal)"),
        ("Declencheur manuel", "Normaliser (multicanal)"),
        ("Normaliser (multicanal)", "PG: enregistrer evenement"),
        ("PG: enregistrer evenement", "PG: resoudre societe"),
        ("PG: resoudre societe", "Assembler le contexte"),
        ("Assembler le contexte", "Agent Claude (triage)"),
        ("Agent Claude (triage)", "Parser + garde-fous"),
        ("Parser + garde-fous", "Est-ce un ticket ?"),
        ("Est-ce un ticket ?", "PG: creer le ticket", 0),   # sortie 0 = vrai
        ("Est-ce un ticket ?", "Ignore (pas un ticket)", 1),  # sortie 1 = faux
        ("PG: creer le ticket", "Router par niveau"),
        ("Router par niveau", "N0 repondre", 0),
        ("Router par niveau", "N1 valider puis executer", 1),
        ("Router par niveau", "N2 guider", 2),
        ("Router par niveau", "N3 escalader", 3),
        ("Webhook reporting", "PG: tickets en cours"),
        ("PG: tickets en cours", "Formater le rapport"),
    ]),
    conn([("Modele Anthropic", "Agent Claude (triage)", 0, "ai_languageModel")]),
)

workflow = {
    "name": "AgentSupport - Pipeline",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1"},
}

if __name__ == "__main__":
    out = os.path.join(HERE, "workflows", "agentsupport-pipeline.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(workflow, f, ensure_ascii=False, indent=2)
    print("ecrit", out, "-", len(nodes), "noeuds")
