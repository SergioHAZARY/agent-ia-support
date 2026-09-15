# -*- coding: utf-8 -*-
"""
Construit LE workflow unique 'AgentSupport - Pipeline' et l'ecrit dans
n8n/workflows/agentsupport-pipeline.json.

Source de verite reproductible : la logique des garde-fous vient de
n8n/guardrails.js, le prompt de prompts/triage.md. Regenerer apres toute
modification :  python n8n/build_pipeline.py

Le fichier JSON produit est deploye vers n8n par n8n/sync.py (et par
l'Action GitHub deploy-n8n.yml).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GUARDRAILS_JS = open(os.path.join(HERE, "guardrails.js"), encoding="utf-8").read()
# on retire la ligne module.exports (inutile et invalide dans un noeud n8n)
GUARDRAILS_JS = GUARDRAILS_JS.replace(
    "module.exports = { applyGuardrails, checkForbidden };", "").rstrip()


def system_prompt():
    t = open(os.path.join(ROOT, "prompts", "triage.md"), encoding="utf-8").read()
    s = t.index("```text") + len("```text")
    e = t.index("```", s)
    return t[s:e].strip()


def node(name, ntype, ver, pos, params=None):
    return {"name": name, "type": ntype, "typeVersion": ver, "position": pos,
            "parameters": params or {}}


def sticky(name, content, pos, w=460, h=220):
    return {"name": name, "type": "n8n-nodes-base.stickyNote", "typeVersion": 1,
            "position": pos, "parameters": {"content": content, "height": h, "width": w}}


def conn(pairs):
    c = {}
    for src, dst, *rest in pairs:
        out_idx = rest[0] if rest else 0
        c.setdefault(src, {"main": []})
        while len(c[src]["main"]) <= out_idx:
            c[src]["main"].append([])
        c[src]["main"][out_idx].append({"node": dst, "type": "main", "index": 0})
    return c


# ----- code des noeuds --------------------------------------------------------
NORMALIZE_JS = r"""
// Entree : update Telegram OU exécution manuelle (message d'exemple).
const t = $json.message || $json.channel_post || null;
if (t) {
  return [{ json: {
    platform: 'telegram',
    external_id: String((t.chat||{}).id||''),
    source_message_id: String(t.message_id||''),
    sender_external_id: String((t.from||{}).id||''),
    sender_raw: [(t.from||{}).first_name,(t.from||{}).last_name,(t.from||{}).username].filter(Boolean).join(' '),
    message_body: t.text || t.caption || '',
  }}];
}
// Exécution manuelle : message d'exemple pour tester le pipeline.
return [{ json: {
  platform:'telegram', external_id:'demo', source_message_id:'1',
  sender_external_id:'0', sender_raw:'Demo (non verifie)',
  message_body: "Bonjour, pouvez-vous m'ajouter a l'outil de design ?",
}}];
""".strip()

ASSEMBLE_JS = r"""
// WF-02 (simplifie) : contexte. Sans base, on part d'un contexte prudent :
// societe en palier P1 (max_autonomy N3), demandeur non verifie.
// Quand PostgreSQL sera branche, remplacer ce bloc par les vraies lectures.
const ctx = {
  max_autonomy: 'N3',
  observation_only: false,
  identity: { verified: false, role: 'employe', email: null },
  runbooks: {},                 // aucun runbook tant que P1
  taxonomy_categories: ['acces_licences','compte_identite','messagerie','poste_travail',
    'reseau','applicatif','developpement','securite','information','hors_perimetre'],
  confidence_floor: 0.7,
};
const b = $json.message_body || '';
const userContent =
  "<demandeur>" + ($json.sender_raw || 'INCONNU') + "</demandeur>\n" +
  "<canal>telegram / " + ($json.external_id||'') + "</canal>\n" +
  "<historique></historique>\n<kb></kb>\n<cas_similaires></cas_similaires>\n" +
  "<runbooks></runbooks>\n\n<message>\n" + b + "\n</message>";
return [{ json: { ctx, userContent, source: $json } }];
""".strip()

PARSE_GUARD_JS = GUARDRAILS_JS + r"""

// --- parse la reponse Claude + applique les garde-fous ---
const ctx = $('Assembler le contexte').item.json.ctx;
let raw;
try {
  const txt = ($json.content || []).map(x => x.text || '').join('');
  raw = JSON.parse(txt);
} catch (e) {
  raw = { is_ticket:true, title:'triage illisible', summary:'', category:'information',
          subcategory:'question_procedure', priority:'p3', autonomy_level:'N3',
          confidence:0, alert:'reponse du modele non parsable' };
}
const r = applyGuardrails(raw, ctx);
return [{ json: Object.assign({}, r.triage, {
  _adjustments: r.adjustments, _alerts: r.alerts,
  _source: $('Assembler le contexte').item.json.source,
}) }];
"""

ANTHROPIC_BODY = {
    "model": "={{ $env.ANTHROPIC_MODEL || 'claude-sonnet-5' }}",
    "max_tokens": 2000,
    "system": system_prompt(),
    "messages": [{"role": "user", "content": "={{ $json.userContent }}"}],
    "output_config": {"format": {"type": "json_schema", "schema": {
        "type": "object",
        "properties": {
            "is_ticket": {"type": "boolean"}, "title": {"type": "string"},
            "summary": {"type": "string"}, "category": {"type": "string"},
            "subcategory": {"type": "string"},
            "priority": {"type": "string", "enum": ["p1", "p2", "p3", "p4"]},
            "autonomy_level": {"type": "string", "enum": ["N0", "N1", "N2", "N3"]},
            "confidence": {"type": "number"},
            "runbook_code": {"type": ["string", "null"]},
            "runbook_params": {"type": "object"},
            "proposed_response": {"type": ["string", "null"]},
            "escalation_reason": {"type": ["string", "null"]},
            "assignee_hint": {"type": ["string", "null"]},
            "missing_info": {"type": "array", "items": {"type": "string"}},
            "alert": {"type": ["string", "null"]},
        },
        "required": ["is_ticket", "title", "summary", "category", "subcategory",
                     "priority", "autonomy_level", "confidence"],
        "additionalProperties": False,
    }}},
}

NOTE = (
    "## AgentSupport - Pipeline (workflow unique)\n\n"
    "Automatise toute la chaine en un seul workflow :\n"
    "Telegram -> normalise -> contexte -> Claude -> GARDE-FOUS -> route N0/N1/N2/N3 -> repond.\n\n"
    "Genere depuis git : n8n/build_pipeline.py (ne pas editer a la main).\n"
    "Deploye par n8n/sync.py et l'Action GitHub deploy-n8n.\n\n"
    "AVANT ACTIVATION :\n"
    "1. Credential Header Auth 'Anthropic' : x-api-key = <cle>.\n"
    "2. Credential Telegram (BotFather, /setprivacy Disable).\n"
    "3. Brancher PostgreSQL dans 'Assembler le contexte' (contexte reel)\n"
    "   et dans les sorties (creation de ticket, reponse)."
)

nodes = [
    sticky("note", NOTE, [-40, -220], w=620, h=300),
    node("Telegram Trigger", "n8n-nodes-base.telegramTrigger", 1.2, [-40, 120],
         {"updates": ["message"], "additionalFields": {}}),
    node("Declencheur manuel", "n8n-nodes-base.manualTrigger", 1, [-40, 320]),
    node("Normaliser", "n8n-nodes-base.code", 2, [220, 220], {"jsCode": NORMALIZE_JS}),
    node("Assembler le contexte", "n8n-nodes-base.code", 2, [440, 220], {"jsCode": ASSEMBLE_JS}),
    node("Appel Claude (triage)", "n8n-nodes-base.httpRequest", 4.2, [660, 220], {
        "method": "POST", "url": "https://api.anthropic.com/v1/messages",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ " + json.dumps(ANTHROPIC_BODY, ensure_ascii=False) + " }}",
        "options": {},
    }),
    node("Parser + garde-fous", "n8n-nodes-base.code", 2, [880, 220], {"jsCode": PARSE_GUARD_JS}),
    node("Router par niveau", "n8n-nodes-base.switch", 3.2, [1100, 220], {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                            "conditions": [{"leftValue": "={{ $json.autonomy_level }}",
                                            "rightValue": lvl,
                                            "operator": {"type": "string", "operation": "equals"}}]},
             "outputKey": lvl}
            for lvl in ["N0", "N1", "N2", "N3"]]},
        "options": {},
    }),
    node("N0 repondre", "n8n-nodes-base.noOp", 1, [1340, -40]),
    node("N1 valider puis executer", "n8n-nodes-base.noOp", 1, [1340, 120]),
    node("N2 guider", "n8n-nodes-base.noOp", 1, [1340, 280]),
    node("N3 escalader", "n8n-nodes-base.noOp", 1, [1340, 440]),
]

connections = conn([
    ("Telegram Trigger", "Normaliser"),
    ("Declencheur manuel", "Normaliser"),
    ("Normaliser", "Assembler le contexte"),
    ("Assembler le contexte", "Appel Claude (triage)"),
    ("Appel Claude (triage)", "Parser + garde-fous"),
    ("Parser + garde-fous", "Router par niveau"),
    ("Router par niveau", "N0 repondre", 0),
    ("Router par niveau", "N1 valider puis executer", 1),
    ("Router par niveau", "N2 guider", 2),
    ("Router par niveau", "N3 escalader", 3),
])

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
