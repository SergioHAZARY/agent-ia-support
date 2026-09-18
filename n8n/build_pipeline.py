# -*- coding: utf-8 -*-
"""
Construit LE workflow unique 'AgentSupport - Pipeline' -> n8n/workflows/agentsupport-pipeline.json

Multicanal (Telegram, Teams/Google Chat/ClickUp/Jira via webhook, email IMAP,
manuel), Agent Claude via OpenRouter (lmChatOpenRouter -> chainLlm), garde-fous en
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


def node(name, ntype, ver, pos, params=None, creds=None, disabled=False,
         on_error=None):
    n = {"name": name, "type": ntype, "typeVersion": ver, "position": pos,
         "parameters": params or {}}
    if creds:
        n["credentials"] = creds
    if disabled:
        n["disabled"] = True
    if on_error:
        n["onError"] = on_error
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
FILTER_TELEGRAM_JS = r"""
// Filtre Telegram :
//  - DM (private) -> toujours traiter
//  - Groupe/supergroupe -> seulement si @itmg_support_bot mentionne ou reply au bot
const j = $json;
const t = j.message || j.channel_post || {};
const chat = t.chat || {};
const chatType = chat.type || '';
const text = (t.text || t.caption || '').toLowerCase();
const BOT_USERNAME = 'itmg_support_bot';

// Ignorer les updates sans contenu exploitable
if (!t.text && !t.caption && !t.photo && !t.document && !t.voice) {
  return [];
}

// DM -> toujours repondre
if (chatType === 'private') {
  return [{ json: j }];
}

// Groupe : verifier @mention ou reply au bot
const mentionInText = text.includes('@' + BOT_USERNAME);
const entities = t.entities || t.caption_entities || [];
const mentionInEntities = entities.some(e => {
  if (e.type === 'mention') {
    const m = (t.text || t.caption || '').substring(e.offset, e.offset + e.length).toLowerCase();
    return m === '@' + BOT_USERNAME;
  }
  return false;
});
const replyToBot = (t.reply_to_message || {}).from && (t.reply_to_message.from.is_bot === true);

if (mentionInText || mentionInEntities || replyToBot) {
  if (t.text) t.text = t.text.replace(new RegExp('@' + BOT_USERNAME, 'gi'), '').trim();
  if (t.caption) t.caption = t.caption.replace(new RegExp('@' + BOT_USERNAME, 'gi'), '').trim();
  return [{ json: Object.assign({}, j, { message: t }) }];
}

return [];
""".strip()

NORMALIZE_JS = r"""
// Normalisation MULTICANAL vers un evenement unique.
// Detecte la source d'apres la forme de l'entree.
const j = $json;
let ev;

if (j.message || j.channel_post) {                 // Telegram
  const t = j.message || j.channel_post;
  // Detecter les photos : extraire le file_id de la plus grande taille
  let photoFileId = '';
  if (t.photo && Array.isArray(t.photo) && t.photo.length > 0) {
    photoFileId = t.photo[t.photo.length - 1].file_id || '';
  } else if (t.document && (t.document.mime_type||'').startsWith('image/')) {
    photoFileId = t.document.file_id || '';
  }
  const caption = t.caption || '';
  const textBody = t.text || caption || (photoFileId ? '[image envoyee]' : '') || (t.document ? '[fichier]' : '') || (t.voice ? '[vocal]' : '') || (t.sticker ? '[sticker]' : '') || '';
  ev = { platform:'telegram', external_id:String((t.chat||{}).id||''),
    source_message_id:String(t.message_id||''),
    sender_external_id:String((t.from||{}).id||''),
    sender_raw:[(t.from||{}).first_name,(t.from||{}).last_name,(t.from||{}).username].filter(Boolean).join(' '),
    message_body: textBody,
    _photo_file_id: photoFileId };
} else if (j.webhookEvent && j.issue) {              // Jira Trigger (webhook natif)
  const iss = j.issue || {};
  const f = iss.fields || {};
  const rep = f.reporter || {};
  const selfUrl = (iss.self || '');
  const site = selfUrl.match(/https?:\/\/([^/]+)/);
  ev = { platform:'jira',
    external_id: site ? site[1] : 'jira',
    source_message_id: String(iss.key || iss.id || Date.now()),
    sender_external_id: String(rep.emailAddress || rep.accountId || ''),
    sender_raw: String(rep.displayName || rep.name || ''),
    message_body: (f.summary || '') + (f.description ? ' — ' + f.description : '') };
} else if (j.headers && j.body && (j.body.issue || j.body.webhookEvent)) {
  // Jira via Webhook multicanal (Jira Automation -> POST /agent-support?platform=jira)
  const b = j.body;
  const iss = b.issue || {};
  const f = iss.fields || {};
  const rep = f.reporter || {};
  const selfUrl = (iss.self || '');
  const site = selfUrl.match(/https?:\/\/([^/]+)/);
  ev = { platform:'jira',
    external_id: site ? site[1] : ((j.query||{}).platform === 'jira' ? 'bzcmtc.atlassian.net' : 'jira'),
    source_message_id: String(iss.key || iss.id || Date.now()),
    sender_external_id: String(rep.emailAddress || rep.accountId || ''),
    sender_raw: String(rep.displayName || rep.name || ''),
    message_body: (f.summary || '') + (f.description ? ' — ' + f.description : '') };
} else if (j.headers && j.body) {                  // Webhook (Teams/GChat/ClickUp)
  const b = j.body || {};
  const plat = (j.query && j.query.platform) || b.platform || 'webhook';
  ev = { platform: plat,
    external_id: String(b.channel_id || b.space || (b.space||{}).name || b.chat_id || b.list_id || ''),
    source_message_id: String(b.message_id || b.eventId || (b.message||{}).name || Date.now()),
    sender_external_id: String(b.user_id || (b.from||{}).id || (b.user||{}).name || ''),
    sender_raw: String(b.user_name || (b.from||{}).user || (b.user||{}).displayName || ''),
    message_body: b.text || (b.message||{}).text || b.description || b.content || '' };
} else if (j.textPlain || j.textHtml || j.subject) {  // Email IMAP / Outlook
  // external_id = adresse de la boite de reception (to), pas l'expediteur
  const toAddr = ((j.to||{}).value||[{}])[0].address || '';
  ev = { platform:'email',
    external_id: String(toAddr).toLowerCase(),
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
// WF-02 : contexte enrichi. Fusionne societe + historique + runbooks + identite.
const pgTenant = $('PG: resoudre societe').item.json;
const pg = pgTenant && pgTenant.tenant_id ? pgTenant : null;

// Enrichissement : historique, cas_similaires, runbooks, identite
let enriched;
try { enriched = $('PG: enrichir contexte').item.json; } catch(e) { enriched = {}; }
const hist = enriched.historique || [];
const similar = enriched.cas_similaires || [];
const rbs = enriched.runbooks || [];
const ident = enriched.identity || null;

const ctx = {
  max_autonomy: (pg && pg.max_autonomy) || 'N3',
  observation_only: pg ? !!pg.observation_only : false,
  identity: ident && ident.verified
    ? { verified: true, role: ident.role || 'employe', email: ident.email || null }
    : { verified: false, role: 'employe', email: null },
  runbooks: {},
  taxonomy_categories: ['acces_licences','compte_identite','messagerie','poste_travail',
    'reseau','applicatif','developpement','securite','information','hors_perimetre'],
  confidence_floor: 0.7,
};

// Construire la map runbooks pour les garde-fous
for (const r of rbs) {
  ctx.runbooks[r.code] = {
    autonomy_level: r.autonomy_level,
    allowed_roles: (r.roles || '').split(',').filter(Boolean),
    manager_approval: false
  };
}

// Source : vision fusionnee ou message brut
let baseEvent;
try { baseEvent = $('Fusionner texte image').item.json; } catch(e) { baseEvent = null; }
if (!baseEvent) baseEvent = $('Normaliser (multicanal)').item.json;
const src = Object.assign({}, baseEvent,
  { tenant_id: (pg && pg.tenant_id) || null });

// Formater les sections de contexte
const histText = hist.length
  ? hist.map(h => '[' + h.ts + '] ' + (h.body || '').slice(0, 200)).join('\n')
  : '';
const similarText = similar.length
  ? similar.map(s => '[' + (s.ref||'?') + '] ' + (s.title||'') + ' -> ' + (s.ai_response||'').slice(0, 200)).join('\n')
  : '';
const rbText = rbs.length
  ? rbs.map(r => r.code + ' (' + r.autonomy_level + ') : ' + r.title
    + (r.preconditions ? ' [Condition: ' + r.preconditions.slice(0,120) + ']' : '')).join('\n')
  : '';
const identText = ident
  ? ident.full_name + ' (' + ident.role + ', verifie: ' + ident.verified + ')'
  : (src.sender_raw || 'INCONNU');

const userContent =
  "<demandeur>" + identText + "</demandeur>\n" +
  "<canal>" + src.platform + " / " + (src.external_id||'') + "</canal>\n" +
  "<historique>" + histText + "</historique>\n" +
  "<kb></kb>\n" +
  "<cas_similaires>" + similarText + "</cas_similaires>\n" +
  "<runbooks>" + rbText + "</runbooks>\n\n" +
  "<message>\n" + (src.message_body||'') + "\n</message>";

return [{ json: { ctx, userContent, source: src,
  llm_prompt: (SYSTEM_PROMPT_PLACEHOLDER) + "\n\n" + userContent } }];
""".strip()

PARSE_GUARD_JS = GUARDRAILS_JS + r"""

// Recupere la sortie de l'Agent Claude (chainLlm : champ text/output), parse, garde-fous.
const ctx = $('Assembler le contexte').item.json.ctx;
const src = $('Assembler le contexte').item.json.source;
let modelText = $json.text || $json.output || '';
if (!modelText && Array.isArray($json.content)) modelText = $json.content.map(b=>b.text||'').join('');

// Parsing robuste : extraire le JSON meme si le modele a ajoute du texte autour
// Etape 0 : stripper les blocs markdown ```json ... ```
modelText = modelText.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/i, '').trim();

let raw;
try { raw = JSON.parse(modelText); }
catch (e) {
  // Essayer d'extraire un objet JSON du texte
  const jsonMatch = modelText.match(/\{[\s\S]*\}/);
  if (jsonMatch) {
    try { raw = JSON.parse(jsonMatch[0]); }
    catch (e2) { raw = null; }
  }
  if (!raw) {
    // Dernier recours : le modele a repondu en texte libre
    const txt = String(modelText).trim();
    if (txt.length > 0 && txt.length < 500) {
      raw = { is_ticket:false, proposed_response:txt, confidence:0.8 };
    } else {
      raw = { is_ticket:true, title:'triage illisible', summary:txt.slice(0,200),
              category:'information', subcategory:'question_procedure', priority:'p3',
              autonomy_level:'N3', confidence:0.5, alert:'reponse du modele non parsable en JSON' };
    }
  }
}

// Garantir les champs obligatoires (title et summary sont NOT NULL dans la table)
if (raw.is_ticket !== false) {
  if (!raw.title) raw.title = raw.summary || src.message_body.slice(0, 120) || 'Demande sans titre';
  if (!raw.summary) raw.summary = raw.title || '';
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

PREPARE_REPLY_TICKET_JS = r"""
// Prepare la reponse pour un TICKET (is_ticket = true).
// NEURONTRIAGE v1 : Branche A (resolved) ou Branche B (escalated).
let src;
try { src = $('Fusionner texte image').item.json; } catch(e) { src = $('Normaliser (multicanal)').item.json; }
const parsed = $('Parser + garde-fous').item.json;
const platform = src.platform || '';
const level = parsed.autonomy_level || 'N3';
const status = parsed.resolution_status || (level === 'N3' ? 'escalated' : 'resolved');

// Recuperer le ticket_ref depuis PG si disponible
let ticketRef = '';
try {
  const pg = $('PG: creer le ticket').item.json;
  ticketRef = pg.ref || ('IT-' + (pg.id || '').toString().slice(0, 5));
} catch(e) { ticketRef = 'IT-' + Date.now().toString().slice(-5); }

// 1. Reponse de Claude (prioritaire)
let reply = (parsed.proposed_response || '').trim();

// 2. Fallback uniquement si Claude n'a rien propose
if (!reply) {
  if (status === 'escalated' || level === 'N3') {
    const reason = parsed.escalation_reason || 'intervention humaine requise';
    reply = "J'ai analyse votre demande et je ne suis pas en mesure de la resoudre "
          + "a mon niveau. Raison : " + reason + ".\n\n"
          + "Votre demande a ete escaladee vers un technicien IT qui prendra le relais.\n"
          + "Reference : " + ticketRef;
  } else {
    reply = parsed.summary || "Votre demande a ete enregistree.";
  }
}

// 3. Pour les escalades, toujours ajouter reference ticket + tache ClickUp
if (status === 'escalated' || level === 'N3') {
  reply += "\n\n---\n📋 Reference ticket : " + ticketRef;
  try {
    const alertData = $('Preparer alerte DEV MG').item.json;
    if (alertData && alertData.task_url) {
      reply += "\n🔗 Tache ClickUp : " + alertData.task_url;
      reply += "\n✅ Un technicien a ete alerte.";
    }
  } catch(e) { /* pas d'escalade ClickUp (erreur ou autre niveau) */ }
}

// Chat ID : vient du trigger Telegram ou du champ external_id
let chat_id = src.external_id || '';
if (platform === 'telegram') {
  try {
    const tg = $('TelegramTrigger').item.json;
    const msg = tg.message || tg.channel_post || {};
    chat_id = String((msg.chat || {}).id || src.external_id || '');
  } catch(e) { /* pas un trigger telegram */ }
}

// Recuperer le ticket_id depuis PG pour l'audit
let ticketId = '';
try { ticketId = $('PG: creer le ticket').item.json.id || ''; } catch(e) {}

return [{ json: { platform, chat_id, reply_text: reply, resolution_status: status,
  ticket_ref: ticketRef, ticket_id: ticketId, autonomy_level: level,
  _source: src } }];
""".strip()

PREPARE_REPLY_NONTICKET_JS = r"""
// Prepare la reponse pour un NON-TICKET (salutations, remerciements, etc.)
let src;
try { src = $('Fusionner texte image').item.json; } catch(e) { src = $('Normaliser (multicanal)').item.json; }
const parsed = $('Parser + garde-fous').item.json;
const platform = src.platform || '';

let reply = (parsed.proposed_response || '').trim();
if (!reply) {
  reply = "Bonjour ! Je suis l'agent de support IT. Comment puis-je vous aider ?";
}

let chat_id = src.external_id || '';
if (platform === 'telegram') {
  try {
    const tg = $('TelegramTrigger').item.json;
    const msg = tg.message || tg.channel_post || {};
    chat_id = String((msg.chat || {}).id || src.external_id || '');
  } catch(e) {}
}

return [{ json: { platform, chat_id, reply_text: reply, resolution_status: 'not_a_ticket',
  ticket_ref: '', ticket_id: '', autonomy_level: 'N0', _source: src } }];
""".strip()

VISION_PREPARE_JS = r"""
// Construit la requete OpenRouter Vision avec l'URL de l'image Telegram.
// Recoit la reponse de TG: getFile qui contient result.file_path.
// On passe l'URL directe a Claude Vision (pas de telechargement binaire).
const src = $('Normaliser (multicanal)').item.json;
const caption = src.message_body || '';
const filePath = ($json.result || {}).file_path || '';

// Construire l'URL de telechargement Telegram (publique, valable ~1h)
const botToken = $vars.TELEGRAM_BOT_TOKEN || '';
const imageUrl = (filePath && botToken)
  ? 'https://api.telegram.org/file/bot' + botToken + '/' + filePath
  : '';

const userContent = [];
if (imageUrl) {
  userContent.push({ type: 'image_url', image_url: { url: imageUrl } });
}
const prompt = caption && caption !== '[image envoyee]'
  ? "L'utilisateur a envoye cette image avec le message : \"" + caption + "\". Decris precisement ce que tu vois dans l'image (texte, erreurs, interfaces). Si c'est une capture d'ecran d'une erreur informatique, identifie le probleme."
  : "L'utilisateur a envoye cette image pour signaler un probleme informatique. Decris precisement ce que tu vois : texte visible, messages d'erreur, interfaces, et identifie le probleme.";
userContent.push({ type: 'text', text: prompt });

return [{ json: {
  model: 'anthropic/claude-sonnet-4',
  max_tokens: 1000,
  messages: [{ role: 'user', content: userContent }]
}}];
""".strip()

VISION_MERGE_JS = r"""
// Fusionne le resultat de la vision avec le message original.
// Le texte extrait de l'image remplace/complete le message_body.
const src = Object.assign({}, $('Normaliser (multicanal)').item.json);
const visionResponse = $json.choices && $json.choices[0] && $json.choices[0].message
  ? $json.choices[0].message.content : '';

if (visionResponse) {
  const caption = src.message_body || '';
  const combined = caption && caption !== '[image envoyee]'
    ? caption + '\n\n[Description de l\'image jointe] ' + visionResponse
    : '[Contenu de l\'image envoyee] ' + visionResponse;
  src.message_body = combined.slice(0, 2000);
}

return [{ json: src }];
""".strip()

PREPARE_ESCALADE_JS = r"""
// Prepare l'escalade N3 : payload ClickUp + message alerte techniciens.
const parsed = $('Parser + garde-fous').item.json;
let src;
try { src = $('Fusionner texte image').item.json; } catch(e) { src = $('Normaliser (multicanal)').item.json; }

let ticketRef = '';
try {
  const pg = $('PG: creer le ticket').item.json;
  ticketRef = pg.ref || ('IT-' + (pg.id || '').toString().slice(0, 5));
} catch(e) { ticketRef = 'IT-' + Date.now().toString().slice(-5); }

// Mapping priorite vers ClickUp (1=urgent, 2=high, 3=normal, 4=low)
const priorityMap = { p1: 1, p2: 2, p3: 3, p4: 4 };
const cuPriority = priorityMap[parsed.priority] || 3;

const taskName = '[' + ticketRef + '] ' + (parsed.title || parsed.summary || 'Escalade agent IA');
const taskDesc = '**Ticket:** ' + ticketRef + '\n'
  + '**Demandeur:** ' + (src.sender_raw || 'Inconnu') + '\n'
  + '**Canal:** ' + (src.platform || '') + ' / ' + (src.external_id || '') + '\n'
  + '**Categorie:** ' + (parsed.category || '') + ' / ' + (parsed.subcategory || '') + '\n'
  + '**Priorite:** ' + (parsed.priority || 'p3') + '\n'
  + '**Raison escalade:** ' + (parsed.escalation_reason || 'Intervention humaine requise') + '\n\n'
  + '**Resume:** ' + (parsed.summary || '') + '\n\n'
  + '**Message original:**\n' + (src.message_body || '').slice(0, 1000) + '\n\n'
  + '**Reponse proposee:**\n' + (parsed.proposed_response || '').slice(0, 1000);

return [{ json: {
  clickup_payload: { name: taskName, markdown_description: taskDesc, priority: cuPriority,
    status: 'Open', tags: ['agent-ia', parsed.category || 'support'].filter(Boolean) },
  ticketRef, platform: src.platform || '',
  sender_raw: src.sender_raw || '',
  escalation_reason: parsed.escalation_reason || 'Intervention humaine requise',
  summary: parsed.summary || parsed.title || '',
  category: parsed.category || '',
  priority: parsed.priority || 'p3'
}}];
""".strip()

CLICKUP_ALERT_JS = r"""
// Construit le message d'alerte HTML pour le groupe Telegram DEV MG apres creation ClickUp.
// HTML au lieu de Markdown : plus robuste si les donnees contiennent * _ [ etc.
const esc = $('Preparer escalade N3').item.json;
const cuResp = $json;
const cuError = cuResp.err || cuResp.error || '';
const taskId = cuResp.id || '';
const taskUrl = taskId ? (cuResp.url || ('https://app.clickup.com/t/' + taskId)) : '';

function esc_html(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

let msg = '⚠️ <b>ESCALADE AGENT IA</b>\n\n'
  + '🆔 <b>Ticket:</b> ' + esc_html(esc.ticketRef) + '\n'
  + '👤 <b>Demandeur:</b> ' + esc_html(esc.sender_raw) + '\n'
  + '📁 <b>Categorie:</b> ' + esc_html(esc.category) + '\n'
  + '🚨 <b>Priorite:</b> ' + esc_html(esc.priority) + '\n\n'
  + '📝 <b>Resume:</b> ' + esc_html((esc.summary || '').slice(0, 300)) + '\n\n'
  + '❗ <b>Raison:</b> ' + esc_html(esc.escalation_reason);
if (taskUrl) {
  msg += '\n\n🔗 <b>Tache ClickUp:</b> <a href="' + taskUrl + '">' + esc_html(taskUrl) + '</a>';
} else if (cuError) {
  msg += '\n\n⚠️ <b>ClickUp indisponible</b> : ' + esc_html(String(cuError).slice(0, 200));
}

return [{ json: { alert_text: msg, task_url: taskUrl, task_id: taskId } }];
""".strip()

N1_EXECUTE_JS = r"""
// N1 : tente d'executer le runbook si un webhook est configure.
// Sinon, la reponse de l'agent contient deja la procedure a suivre.
const parsed = $('Parser + garde-fous').item.json;
const rbCode = parsed.runbook_code || '';
let enriched;
try { enriched = $('PG: enrichir contexte').item.json; } catch(e) { enriched = {}; }
const rbs = enriched.runbooks || [];
const rb = rbs.find(r => r.code === rbCode);

const result = {
  has_webhook: false,
  webhook_url: '',
  runbook_code: rbCode,
  runbook_params: parsed.runbook_params || {},
  runbook_title: rb ? rb.title : rbCode
};
if (rb && rb.n8n_webhook) {
  result.has_webhook = true;
  result.webhook_url = rb.n8n_webhook;
}
return [{ json: result }];
""".strip()

JIRA_COMMENT_JS = r"""
// Prepare le commentaire Jira : extraire l'issue key et le site depuis source_message_id.
const reply = $json.reply_text || '';
const src = $json._source || {};
const issueKey = src.source_message_id || '';
const extId = src.external_id || '';
const site = extId.includes('.') ? extId : 'bzcmtc.atlassian.net';
return [{ json: {
  url: 'https://' + site + '/rest/api/3/issue/' + issueKey + '/comment',
  body: { body: { type: 'doc', version: 1, content: [
    { type: 'paragraph', content: [{ type: 'text', text: reply.slice(0, 2000) }] }
  ]}}
}}];
""".strip()

CLICKUP_COMMENT_JS = r"""
// Prepare le commentaire ClickUp pour les demandes arrivees depuis ClickUp.
const reply = $json.reply_text || '';
const src = $json._source || {};
const taskId = src.source_message_id || '';
return [{ json: {
  url: 'https://api.clickup.com/api/v2/task/' + taskId + '/comment',
  body: { comment_text: reply.slice(0, 2000) }
}}];
""".strip()

DETECT_REPORTING_JS = r"""
// Detecte si le message est une demande de consultation de tickets/reporting.
// Arrive uniquement sur la branche non-ticket (is_ticket = false).
const parsed = $json;
const body = ((parsed._source || {}).message_body || '').toLowerCase();

// Mots-cles de consultation de tickets (FR + EN)
const isReporting = (
  /\btickets?\b/.test(body) && /(liste|lister|montre|affich|voir|consulter|en\s*cours|ouverts?|clos|ferm|suivi|combien|nombre|status|etat|show|list|display|get)/i.test(body)
) || /\b(reporting|rapport\b.*ticket|suivi\b.*demande)/i.test(body);

return [{ json: Object.assign({}, parsed, { _is_reporting: isReporting }) }];
""".strip()

FORMAT_REPORT_DM_JS = r"""
// Formate la liste des tickets en reponse lisible pour le DM.
const tickets = $input.all().map(i => i.json);
let src;
try { src = $('Fusionner texte image').item.json; } catch(e) { src = $('Normaliser (multicanal)').item.json; }

let reply = '';
if (tickets.length === 0) {
  reply = "Aucun ticket en cours actuellement. Tous les problemes ont ete resolus !";
} else {
  reply = "📋 *Tickets en cours* (" + tickets.length + ")\n\n";
  for (const t of tickets) {
    const prio = {'p1':'🔴','p2':'🟠','p3':'🟡','p4':'🟢'}[t.priority] || '⚪';
    reply += prio + " *" + (t.ref || t.id.slice(0,8)) + "* — " + (t.title||'').slice(0,60) + "\n"
      + "   " + (t.category||'') + " | " + (t.status||'') + " | " + (t.autonomy_level||'') + "\n\n";
  }
  reply += "Pour plus de details sur un ticket, donne-moi sa reference.";
}

let chat_id = src.external_id || '';
if ((src.platform || '') === 'telegram') {
  try {
    const tg = $('TelegramTrigger').item.json;
    const msg = tg.message || tg.channel_post || {};
    chat_id = String((msg.chat || {}).id || src.external_id || '');
  } catch(e) {}
}

return [{ json: { platform: src.platform || 'telegram', chat_id, reply_text: reply,
  resolution_status: 'not_a_ticket', ticket_ref: '', ticket_id: '',
  autonomy_level: 'N0', _source: src } }];
""".strip()

NOTIFY_TICKET_JS = r"""
// Notification breve HTML pour le groupe DEV MG a chaque nouveau ticket.
const parsed = $('Parser + garde-fous').item.json;
const src = parsed._source || {};
const pg = $json;
const ticketRef = pg.ref || ('IT-' + (pg.id || '').toString().slice(0, 8));
const level = parsed.autonomy_level || 'N0';
const prio = parsed.priority || 'p3';
const prioIcon = {'p1':'🔴','p2':'🟠','p3':'🟡','p4':'🟢'}[prio] || '⚪';

function esc_html(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

const msg = '📩 <b>Nouveau ticket</b> ' + esc_html(ticketRef) + '\n'
  + prioIcon + ' ' + esc_html(prio.toUpperCase()) + ' | ' + esc_html(level)
  + ' | ' + esc_html(parsed.category || '') + '\n'
  + '👤 ' + esc_html(src.sender_raw || 'Inconnu')
  + ' (' + esc_html(src.platform || '') + ')\n'
  + '📝 ' + esc_html((parsed.title || parsed.summary || '').slice(0, 120));

return [{ json: { notif_text: msg } }];
""".strip()

# ----------- Admin Bot (@itmg_admin_bot) — branche reporting en temps reel ---

ADMIN_PARSE_JS = r"""
// Parse la commande admin du bot @itmg_admin_bot.
const j = $json;
const msg = j.message || j.channel_post || {};
const text = (msg.text || '').toLowerCase().trim();
const chatId = String((msg.chat || {}).id || '');
const chatType = (msg.chat || {}).type || 'private';

let cmd = 'tickets';
let groupBy = 'categorie';
let statusFilter = '';

if (/^\/(start)/.test(text)) cmd = 'start';
else if (/^\/(help)|aide/.test(text)) cmd = 'help';
else if (/rapport|report|bilan|synthes/.test(text)) { cmd = 'rapport'; }
else if (/stats|statistiq/.test(text)) { cmd = 'stats'; }
else if (/recent|derniers?|24h|nouveau/.test(text)) { cmd = 'recents'; }
else if (/canal|canaux|plateforme|channel/.test(text)) groupBy = 'canal';
else if (/departement|societe|tenant|entite/.test(text)) groupBy = 'tenant';
else if (/priorite|urgence|p1|p2/.test(text)) groupBy = 'priorite';
else if (/ticket|demande|en.cours|ouvert/.test(text)) cmd = 'tickets';

if (/\b(ouverts?|open)\b/.test(text)) statusFilter = 'ouvert';
else if (/\b(en.cours|in.progress|traitement)\b/.test(text)) statusFilter = 'en_cours';
else if (/\b(fini|termin|clos|closed|ferm|resolu|resolved)\b/.test(text)) statusFilter = 'clos';

return [{ json: { cmd, groupBy, chatId, chatType, text, statusFilter } }];
""".strip()

ADMIN_FORMAT_JS = r"""
// Formate la reponse admin avec groupement par categorie/canal/priorite/tenant.
const cmdData = $('Parser admin').first().json;
const cmd = cmdData.cmd;
const groupBy = cmdData.groupBy;
const chatId = cmdData.chatId;
const tickets = $input.all().map(i => i.json).filter(t => t.id);

function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
const prioIcon = {'p1':'🔴','p2':'🟠','p3':'🟡','p4':'🟢'};

let reply = '';

if (cmd === 'start') {
  reply = '🤖 <b>IT MG Admin Bot</b>\n\n'
    + 'Je suis le bot administrateur. Je surveille tous les canaux en temps reel.\n\n'
    + '<b>Commandes :</b>\n'
    + '/tickets — Tickets en cours (par categorie)\n'
    + '<i>tickets par canal</i> — Grouper par plateforme\n'
    + '<i>tickets par priorite</i> — Grouper par priorite\n'
    + '<i>tickets par departement</i> — Grouper par societe\n'
    + '/rapport — Rapport complet par statut (tous canaux)\n'
    + '/recents — Dernieres demandes (24h)\n'
    + '/stats — Statistiques globales\n'
    + '/help — Cette aide\n\n'
    + '📡 Les notifications de nouveaux tickets arrivent automatiquement dans le groupe DEV MG.';
  return [{ json: { reply, chatId } }];
}

if (cmd === 'help') {
  reply = '📋 <b>Aide IT MG Admin</b>\n\n'
    + '• <code>tickets</code> — tous les tickets en cours par categorie\n'
    + '• <code>tickets par canal</code> — groupes par plateforme (Telegram, Jira, email...)\n'
    + '• <code>tickets par priorite</code> — groupes par urgence\n'
    + '• <code>tickets par departement</code> — groupes par societe\n'
    + '• <code>rapport</code> — rapport complet par statut (tous canaux)\n'
    + '• <code>rapport ouverts</code> — uniquement les tickets ouverts\n'
    + '• <code>rapport en cours</code> — uniquement les tickets en traitement\n'
    + '• <code>rapport clos</code> — uniquement les tickets termines\n'
    + '• <code>recents</code> — tickets des dernieres 24h\n'
    + '• <code>stats</code> — comptages et repartition';
  return [{ json: { reply, chatId } }];
}

const statusFilter = cmdData.statusFilter || '';
const statusMap = {
  'ouvert': ['ouvert','open','nouveau','new'],
  'en_cours': ['en_cours','in_progress','en cours','attente','pending','escalade'],
  'clos': ['clos','closed','resolu','resolved','termine','done','annule','cancelled']
};

function matchStatus(s, filter) {
  if (!filter) return true;
  const ls = String(s||'').toLowerCase();
  return (statusMap[filter]||[]).some(k => ls.includes(k) || ls === k);
}

const statusLabel = {'ouvert':'🟢 Ouverts','en_cours':'🔵 En cours','clos':'✅ Clos/Termines'};
const statusIcon = s => {
  const ls = String(s||'').toLowerCase();
  if (statusMap.clos.some(k => ls.includes(k))) return '✅';
  if (statusMap.en_cours.some(k => ls.includes(k))) return '🔵';
  return '🟢';
};

// Commande /rapport : rapport complet par statut, canal, categorie
if (cmd === 'rapport') {
  let pool = tickets;
  if (statusFilter) pool = tickets.filter(t => matchStatus(t.status, statusFilter));

  if (pool.length === 0) {
    reply = '📭 Aucun ticket' + (statusFilter ? ' avec le statut "' + esc(statusFilter) + '"' : '') + '.';
    return [{ json: { reply, chatId } }];
  }

  const title = statusFilter
    ? '📊 <b>Rapport — ' + esc(statusLabel[statusFilter] || statusFilter) + '</b>'
    : '📊 <b>Rapport complet — tous statuts</b>';

  // Compteurs globaux par statut
  const byStatus = {};
  for (const t of pool) {
    const s = t.status || 'inconnu';
    byStatus[s] = (byStatus[s]||0) + 1;
  }

  reply = title + ' (' + pool.length + ' tickets)\n\n';

  if (!statusFilter) {
    reply += '<b>Repartition par statut :</b>\n';
    for (const [s,n] of Object.entries(byStatus).sort((a,b) => b[1]-a[1]))
      reply += '  ' + statusIcon(s) + ' ' + esc(s) + ' : ' + n + '\n';
    reply += '\n';
  }

  // Par canal
  const byPlatform = {};
  for (const t of pool) {
    const p = t.platform || 'inconnu';
    if (!byPlatform[p]) byPlatform[p] = [];
    byPlatform[p].push(t);
  }
  reply += '<b>Par canal :</b>\n';
  for (const [plat, items] of Object.entries(byPlatform).sort((a,b) => b[1].length - a[1].length)) {
    reply += '\n📡 <b>' + esc(plat.toUpperCase()) + '</b> (' + items.length + ')\n';
    for (const t of items.slice(0, 5)) {
      const p = prioIcon[t.priority] || '⚪';
      reply += '  ' + statusIcon(t.status) + p + ' <b>' + esc(t.ref || (t.id||'').slice(0,8)) + '</b> — '
        + esc((t.title||'').slice(0,45)) + '\n'
        + '    ' + esc(t.status||'') + ' | ' + esc(t.category||'') + ' | '
        + esc(t.tenant_name||'') + ' | ' + esc(t.cree_le||'') + '\n';
    }
    if (items.length > 5) reply += '  <i>... et ' + (items.length - 5) + ' autres</i>\n';
  }

  // Par categorie
  const byCat = {};
  for (const t of pool) {
    const c = t.category || 'autre';
    byCat[c] = (byCat[c]||0) + 1;
  }
  reply += '\n<b>Par categorie :</b>\n';
  for (const [c,n] of Object.entries(byCat).sort((a,b) => b[1]-a[1]))
    reply += '  • ' + esc(c) + ' : ' + n + '\n';

  return [{ json: { reply, chatId } }];
}

// Filtrer les recents (created_at < 24h) cote JS
let filtered = tickets.filter(t => !matchStatus(t.status, 'clos'));
if (cmd === 'recents') {
  const cutoff = new Date(Date.now() - 24*3600*1000).toISOString();
  filtered = tickets.filter(t => (t.cree_le_iso || t.created_at || '') > cutoff);
}

if (filtered.length === 0) {
  reply = cmd === 'recents'
    ? '✅ Aucune nouvelle demande dans les dernieres 24h.'
    : '✅ Aucun ticket en cours. Tout est resolu !';
  return [{ json: { reply, chatId } }];
}

if (cmd === 'stats') {
  const byCat = {}, byPrio = {}, byPlatform = {}, byLevel = {};
  for (const t of filtered) {
    byCat[t.category||'autre'] = (byCat[t.category||'autre']||0) + 1;
    byPrio[t.priority||'p4'] = (byPrio[t.priority||'p4']||0) + 1;
    byPlatform[t.platform||'inconnu'] = (byPlatform[t.platform||'inconnu']||0) + 1;
    byLevel[t.autonomy_level||'N0'] = (byLevel[t.autonomy_level||'N0']||0) + 1;
  }
  reply = '📊 <b>Statistiques</b> (' + filtered.length + ' tickets en cours)\n\n';
  reply += '<b>Par categorie :</b>\n';
  for (const [k,v] of Object.entries(byCat).sort((a,b) => b[1]-a[1]))
    reply += '  • ' + esc(k) + ' : ' + v + '\n';
  reply += '\n<b>Par priorite :</b>\n';
  for (const k of ['p1','p2','p3','p4'])
    if (byPrio[k]) reply += '  ' + (prioIcon[k]||'') + ' ' + k.toUpperCase() + ' : ' + byPrio[k] + '\n';
  reply += '\n<b>Par canal :</b>\n';
  for (const [k,v] of Object.entries(byPlatform).sort((a,b) => b[1]-a[1]))
    reply += '  • ' + esc(k) + ' : ' + v + '\n';
  reply += '\n<b>Par niveau :</b>\n';
  for (const k of ['N0','N1','N2','N3'])
    if (byLevel[k]) reply += '  • ' + k + ' : ' + byLevel[k] + '\n';
  return [{ json: { reply, chatId } }];
}

// Groupement standard
const groups = {};
for (const t of filtered) {
  let key;
  if (groupBy === 'canal') key = t.platform || 'inconnu';
  else if (groupBy === 'tenant') key = t.tenant_name || 'non attribue';
  else if (groupBy === 'priorite') key = t.priority || 'p4';
  else key = t.category || 'autre';
  if (!groups[key]) groups[key] = [];
  groups[key].push(t);
}

const label = cmd === 'recents' ? 'Demandes recentes (24h)' : 'Tickets en cours';
reply = '📋 <b>' + label + '</b> (' + filtered.length + ') — par ' + esc(groupBy) + '\n\n';

for (const [group, items] of Object.entries(groups).sort((a,b) => a[0].localeCompare(b[0]))) {
  reply += '📁 <b>' + esc(group.toUpperCase()) + '</b> (' + items.length + ')\n';
  for (const t of items.slice(0, 8)) {
    const p = prioIcon[t.priority] || '⚪';
    reply += '  ' + p + ' <b>' + esc(t.ref || (t.id||'').slice(0,8)) + '</b> — '
      + esc((t.title||'').slice(0,50)) + '\n'
      + '    ' + esc(t.autonomy_level||'') + ' | ' + esc(t.status||'')
      + (t.tenant_name && t.tenant_name !== 'non attribue' ? ' | ' + esc(t.tenant_name) : '')
      + (t.platform ? ' | ' + esc(t.platform) : '') + '\n';
  }
  if (items.length > 8) reply += '  <i>... et ' + (items.length - 8) + ' autres</i>\n';
  reply += '\n';
}

return [{ json: { reply, chatId } }];
""".strip()

JIRA_DEDUP_JS = r"""
// Reformate chaque issue Jira en format normalise (comme si c'etait un webhook).
// Le deduplication se fait dans PG: enregistrer evenement (ON CONFLICT).
const iss = $json;
const f = iss.fields || {};
const rep = f.reporter || {};
const selfUrl = (iss.self || '');
const site = selfUrl.match(/https?:\/\/([^/]+)/);
const desc = typeof f.description === 'string' ? f.description :
  (f.description && f.description.content ? f.description.content.map(
    b => (b.content||[]).map(c => c.text||'').join('')).join(' ') : '');
return [{json: {
  webhookEvent: 'jira:issue_created',
  issue: { id: iss.id, key: iss.key, self: iss.self, fields: {
    summary: f.summary || '', description: desc,
    reporter: rep, project: f.project, issuetype: f.issuetype,
    priority: f.priority, status: f.status }}
}}];
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

# Requete pour la consultation de tickets en DM (reporting conversationnel)
PG_REPORT_DM_SQL = (
    "SELECT id::text, ref, title, category, priority, status, autonomy_level,"
    " to_char(created_at, 'DD/MM HH24:MI') as cree_le"
    " FROM tickets"
    " WHERE status NOT IN ('clos', 'annule')"
    " ORDER BY CASE priority WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 WHEN 'p3' THEN 3 ELSE 4 END,"
    " created_at DESC LIMIT 20;")

PG_ADMIN_TICKETS_SQL = (
    "SELECT t.id::text, t.ref, t.title, t.category, t.subcategory,"
    " t.priority, t.status, t.autonomy_level,"
    " COALESCE(te.name, 'non attribue') as tenant_name,"
    " COALESCE(ch.platform, 'inconnu') as platform,"
    " to_char(t.created_at, 'DD/MM HH24:MI') as cree_le,"
    " t.created_at as cree_le_iso"
    " FROM tickets t"
    " LEFT JOIN tenants te ON te.id = t.tenant_id"
    " LEFT JOIN channels ch ON ch.id = t.channel_id"
    " ORDER BY t.created_at DESC LIMIT 100;")

# Enrichissement du contexte en une seule requete : historique, cas_similaires,
# runbooks disponibles, et identite du demandeur.
# $1=sender_raw $2=platform $3=external_id $4=tenant_id $5=sender_external_id
PG_ENRICH_SQL = (
    "SELECT"
    " (SELECT COALESCE(json_agg(h), '[]'::json) FROM ("
    "   SELECT body, to_char(received_at, 'YYYY-MM-DD HH24:MI') as ts"
    "   FROM events"
    "   WHERE sender_raw = $1"
    "   AND channel_id IN (SELECT id FROM channels WHERE platform = $2 AND external_id = $3)"
    "   ORDER BY received_at DESC OFFSET 1 LIMIT 5"
    " ) h) AS historique,"
    " (SELECT COALESCE(json_agg(s), '[]'::json) FROM ("
    "   SELECT ref, title, summary, ai_response, category"
    "   FROM tickets WHERE tenant_id = $4::uuid AND status IN ('resolu','clos')"
    "   ORDER BY created_at DESC LIMIT 5"
    " ) s) AS cas_similaires,"
    " (SELECT COALESCE(json_agg(r), '[]'::json) FROM ("
    "   SELECT code, title, category, autonomy_level,"
    "     array_to_string(allowed_roles, ',') as roles, preconditions"
    "   FROM v_runbooks_disponibles WHERE tenant_id = $4::uuid"
    " ) r) AS runbooks,"
    " (SELECT row_to_json(i) FROM ("
    "   SELECT id::text, full_name, email, role, verified"
    "   FROM identities WHERE tenant_id = $4::uuid AND active = true"
    "   AND (telegram_user_id = $5 OR teams_user_id = $5 OR google_user_id = $5"
    "     OR email ILIKE $5"
    "     OR (length($1) > 2 AND full_name ILIKE '%' || $1 || '%'))"
    "   LIMIT 1"
    " ) i) AS identity;")

# Audit : enregistre ce qui a ete envoye + trace dans ticket_events
PG_AUDIT_SQL = (
    "WITH upd AS ("
    "  UPDATE tickets SET final_response = $1,"
    "    first_response_at = COALESCE(first_response_at, now())"
    "  WHERE id = $2::uuid RETURNING id"
    "), evt AS ("
    "  INSERT INTO ticket_events (ticket_id, actor, action, payload)"
    "  SELECT $2::uuid, 'agent', 'reponse',"
    "    json_build_object('platform', $3, 'autonomy_level', $4,"
    "      'delivered', $5::boolean)::jsonb"
    "  WHERE EXISTS (SELECT 1 FROM upd)"
    "  RETURNING id"
    ") SELECT (SELECT id FROM upd) as ticket_id,"
    "  (SELECT id FROM evt) as event_id;")


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
    "## AgentSupport - Pipeline v2 (multicanal, contexte enrichi)\n\n"
    "CANAUX : Telegram | Webhook (Jira, Teams, ClickUp, GChat) | Email IMAP.\n"
    "Normalise -> PG events -> societe -> ENRICHIR CONTEXTE -> AGENT CLAUDE\n"
    "-> GARDE-FOUS (regle 5) -> ticket -> route N0/N1/N2/N3.\n\n"
    "REPORTING : detection auto des demandes de tickets -> requete PG.\n"
    "NOTIFICATION : chaque nouveau ticket alerte le groupe DEV MG.\n"
    "N3 -> ClickUp (Escalades Agent IA) + alerte detaillee DEV MG.\n\n"
    "LIVRAISON MULTI-CANAL : Telegram | Email | Jira comment | ClickUp comment.\n"
    "Audit PG : final_response + ticket_events sur chaque reponse.\n\n"
    "Genere depuis git (n8n/build_pipeline.py). Ne pas editer a la main.\n\n"
    "WEBHOOKS :\n"
    "  /webhook/agent-support         (generique)\n"
    "  /webhook/agent-support-jira    (Jira)\n"
    "  /webhook/agent-support-teams   (Teams)\n"
    "  /webhook/agent-support-reporting (GET, reporting API)\n\n"
    "VARIABLES N8N A CREER :\n"
    "CLICKUP_API_TOKEN, CLICKUP_LIST_ID, TELEGRAM_DEVMG_CHAT_ID,\n"
    "TELEGRAM_BOT_TOKEN, SMTP_FROM (optionnel)"
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
    # Email IMAP : désactivé tant que la credential IMAP n'est pas créée dans n8n.
    node("Email IMAP (Outlook)", "n8n-nodes-base.emailReadImap", 2, [-60, 380],
         {"options": {}},
         creds=({"imap": CREDS["imap"]} if "imap" in CREDS else None),
         disabled=("imap" not in CREDS)),
    # Jira : le polling est fait par scripts/poll_jira.py (local) car n8n cloud
    # est bloqué par les restrictions IP Atlassian. Les events arrivent directement
    # dans la table events de Supabase.
    # Webhooks dedies par plateforme (prets a recevoir quand les comptes sont configures)
    node("Webhook Jira", "n8n-nodes-base.webhook", 1.1, [-60, 440], {
        "httpMethod": "POST", "path": "agent-support-jira",
        "responseMode": "onReceived", "options": {}}),
    node("Webhook Teams", "n8n-nodes-base.webhook", 1.1, [-60, 540], {
        "httpMethod": "POST", "path": "agent-support-teams",
        "responseMode": "onReceived", "options": {}}),
    node("Declencheur manuel", "n8n-nodes-base.manualTrigger", 1, [-60, 660]),

    # --- Filtre Telegram (mention @itmg_support_bot ou DM) ---
    node("Filtre Telegram (mention)", "n8n-nodes-base.code", 2, [100, 60],
         {"jsCode": FILTER_TELEGRAM_JS}),

    # --- Pipeline principal ---
    node("Normaliser (multicanal)", "n8n-nodes-base.code", 2, [280, 240],
         {"jsCode": NORMALIZE_JS}),

    # --- Vision : traitement des images ---
    node("A une image ?", "n8n-nodes-base.if", 2.3, [460, 240], {
        "conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                       "combinator": "and",
                       "conditions": [
                           {"id": "img1",
                            "leftValue": "={{ $json._photo_file_id }}",
                            "rightValue": "",
                            "operator": {"type": "string", "operation": "notEquals"}},
                           {"id": "tok1",
                            "leftValue": "={{ $vars.TELEGRAM_BOT_TOKEN }}",
                            "rightValue": "",
                            "operator": {"type": "string", "operation": "notEquals"}}]},
        "options": {}}),
    # Branche image : telecharger via Telegram API
    node("TG: getFile", "n8n-nodes-base.httpRequest", 4.2, [640, 120], {
        "method": "GET",
        "url": '=https://api.telegram.org/bot{{ $vars.TELEGRAM_BOT_TOKEN }}/getFile?file_id={{ $json._photo_file_id }}',
        "options": {"response": {"response": {"responseFormat": "json"}}}}),
    node("Preparer vision", "n8n-nodes-base.code", 2, [860, 120],
         {"jsCode": VISION_PREPARE_JS}),
    node("OpenRouter: vision", "n8n-nodes-base.httpRequest", 4.2, [1080, 120], {
        "method": "POST",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "openRouterApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json) }}",
        "options": {"response": {"response": {"responseFormat": "json"}}},
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "Content-Type", "value": "application/json"}]}},
        creds=({"openRouterApi": CREDS["openRouterApi"]} if "openRouterApi" in CREDS else None)),
    node("Fusionner texte image", "n8n-nodes-base.code", 2, [1300, 120],
         {"jsCode": VISION_MERGE_JS}),

    pg_node("PG: enregistrer evenement", [640, 380], PG_EVENT_SQL,
            "={{ [$json.source_message_id, $json.sender_raw, $json.message_body, "
            "$json.platform, $json.external_id] }}"),
    pg_node("PG: resoudre societe", [660, 240], PG_CTX_SQL,
            "={{ [$('Normaliser (multicanal)').item.json.platform, "
            "$('Normaliser (multicanal)').item.json.external_id] }}"),
    pg_node("PG: enrichir contexte", [780, 380], PG_ENRICH_SQL,
            "={{ [$('Normaliser (multicanal)').item.json.sender_raw, "
            "$('Normaliser (multicanal)').item.json.platform, "
            "$('Normaliser (multicanal)').item.json.external_id, "
            "$('PG: resoudre societe').item.json.tenant_id || null, "
            "$('Normaliser (multicanal)').item.json.sender_external_id || ''] }}"),
    node("Assembler le contexte", "n8n-nodes-base.code", 2, [1000, 240],
         {"jsCode": ASSEMBLE_JS}),

    # --- Agent Claude via OpenRouter ---
    node("Agent Claude (triage)", "@n8n/n8n-nodes-langchain.chainLlm", 1.5, [1120, 240],
         {"promptType": "define", "text": "={{ $json.llm_prompt }}"}),
    node("Modele OpenRouter", "@n8n/n8n-nodes-langchain.lmChatOpenRouter", 1, [1120, 460], {
        "model": "anthropic/claude-sonnet-4",
        "options": {"maxTokensToSample": 2000, "temperature": 0}},
        creds=({"openRouterApi": CREDS["openRouterApi"]} if "openRouterApi" in CREDS else None)),

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
    # Non-ticket : detecter si c'est une demande de reporting/tickets
    node("Detecter reporting", "n8n-nodes-base.code", 2, [1760, -20],
         {"jsCode": DETECT_REPORTING_JS}),
    node("Est-ce un reporting ?", "n8n-nodes-base.if", 2.3, [1940, -20], {
        "conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                       "combinator": "and",
                       "conditions": [{"id": "rpt1",
                                       "leftValue": "={{ $json._is_reporting }}",
                                       "rightValue": "",
                                       "operator": {"type": "boolean", "operation": "true",
                                                    "singleValue": True}}]},
        "options": {}}),
    pg_node("PG: lister tickets", [2120, -100], PG_REPORT_DM_SQL, ""),
    node("Formater rapport DM", "n8n-nodes-base.code", 2, [2340, -100],
         {"jsCode": FORMAT_REPORT_DM_JS}),
    # Non-ticket classique (salutations, merci, etc.)
    node("Preparer reponse (non-ticket)", "n8n-nodes-base.code", 2, [2120, 40],
         {"jsCode": PREPARE_REPLY_NONTICKET_JS}),
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
    node("N1 executer runbook", "n8n-nodes-base.code", 2, [2260, 260],
         {"jsCode": N1_EXECUTE_JS}),
    node("Runbook a webhook ?", "n8n-nodes-base.if", 2.3, [2400, 200], {
        "conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                       "combinator": "and",
                       "conditions": [{"id": "rw1",
                                       "leftValue": "={{ $json.has_webhook }}",
                                       "rightValue": "",
                                       "operator": {"type": "boolean", "operation": "true",
                                                    "singleValue": True}}]},
        "options": {}}),
    node("Appeler webhook runbook", "n8n-nodes-base.httpRequest", 4.2, [2560, 140], {
        "method": "POST",
        "url": "={{ $json.webhook_url }}",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.runbook_params) }}",
        "options": {"response": {"response": {"responseFormat": "json"}}}}),
    node("N2 guider", "n8n-nodes-base.noOp", 1, [2260, 400]),
    # --- Notification DEV MG pour TOUT nouveau ticket (toutes plateformes) ---
    node("Notifier nouveau ticket", "n8n-nodes-base.code", 2, [2020, 480],
         {"jsCode": NOTIFY_TICKET_JS}),
    node("TG: notif DEV MG", "n8n-nodes-base.telegram", 1.2, [2240, 480], {
        "operation": "sendMessage",
        "chatId": "={{ $vars.TELEGRAM_DEVMG_CHAT_ID || '-5219441607' }}",
        "text": "={{ $json.notif_text }}",
        "additionalFields": {"parse_mode": "HTML", "appendAttribution": False}},
        creds=({"telegramApi": CREDS["telegramApi"]} if "telegramApi" in CREDS else None),
        on_error="continueRegularOutput"),
    # --- Escalade N3 : ClickUp + alerte Telegram DEV MG ---
    node("Preparer escalade N3", "n8n-nodes-base.code", 2, [2260, 540],
         {"jsCode": PREPARE_ESCALADE_JS}),
    node("ClickUp: creer tache", "n8n-nodes-base.httpRequest", 4.2, [2500, 540], {
        "method": "POST",
        "url": "=https://api.clickup.com/api/v2/list/{{ $vars.CLICKUP_LIST_ID || '901222267724' }}/task",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "Authorization", "value": "={{ $vars.CLICKUP_API_TOKEN }}"},
            {"name": "Content-Type", "value": "application/json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.clickup_payload) }}",
        "options": {"response": {"response": {"responseFormat": "json"}}}},
        on_error="continueRegularOutput"),
    node("Preparer alerte DEV MG", "n8n-nodes-base.code", 2, [2740, 540],
         {"jsCode": CLICKUP_ALERT_JS}),
    node("Telegram: alerter techniciens", "n8n-nodes-base.telegram", 1.2, [2980, 540], {
        "operation": "sendMessage",
        "chatId": "={{ $vars.TELEGRAM_DEVMG_CHAT_ID || '-5219441607' }}",
        "text": "={{ $json.alert_text }}",
        "additionalFields": {"parse_mode": "HTML", "appendAttribution": False}},
        creds=({"telegramApi": CREDS["telegramApi"]} if "telegramApi" in CREDS else None),
        on_error="continueRegularOutput"),

    # --- Reponse + Livraison multi-canal unifiee ---
    node("Preparer reponse", "n8n-nodes-base.code", 2, [2600, 300], {"jsCode": PREPARE_REPLY_TICKET_JS}),
    # Switch par plateforme : telegram / email / jira / clickup / autre
    node("Router par canal", "n8n-nodes-base.switch", 3.2, [2840, 300], {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                            "conditions": [{"leftValue": "={{ $json.platform }}",
                                            "rightValue": p,
                                            "operator": {"type": "string", "operation": "equals"}}]},
             "outputKey": p} for p in ["telegram", "email", "jira", "clickup"]]},
        "options": {"fallbackOutput": "extra"}}),
    node("Telegram: envoyer reponse", "n8n-nodes-base.telegram", 1.2, [3100, 120], {
        "operation": "sendMessage",
        "chatId": "={{ $json.chat_id }}",
        "text": "={{ $json.reply_text }}",
        "additionalFields": {"parse_mode": "Markdown", "appendAttribution": False}},
        creds=({"telegramApi": CREDS["telegramApi"]} if "telegramApi" in CREDS else None)),
    # Email : desactive tant que SMTP n'est pas configure (le ticket est enregistre en PG)
    node("Email: repondre", "n8n-nodes-base.emailSend", 2.1, [3100, 260], {
        "fromEmail": "={{ $vars.SMTP_FROM || 'support@exemple.com' }}",
        "toEmail": "={{ $json._source.sender_external_id || '' }}",
        "subject": "=Re: {{ $json.ticket_ref || 'Support IT' }}",
        "text": "={{ $json.reply_text }}",
        "options": {}},
        disabled=("smtp" not in CREDS)),
    # Jira : commenter l'issue d'origine
    node("Preparer commentaire Jira", "n8n-nodes-base.code", 2, [3100, 400],
         {"jsCode": JIRA_COMMENT_JS}),
    node("Jira: commenter", "n8n-nodes-base.httpRequest", 4.2, [3320, 400], {
        "method": "POST",
        "url": "={{ $json.url }}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpBasicAuth",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.body) }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "Content-Type", "value": "application/json"}]},
        "options": {"response": {"response": {"responseFormat": "json"}}}},
        creds=({"httpBasicAuth": CREDS["jiraHttp"]} if "jiraHttp" in CREDS else None)),
    # ClickUp : commenter la tache d'origine
    node("Preparer commentaire ClickUp", "n8n-nodes-base.code", 2, [3100, 540],
         {"jsCode": CLICKUP_COMMENT_JS}),
    node("ClickUp: commenter", "n8n-nodes-base.httpRequest", 4.2, [3320, 540], {
        "method": "POST",
        "url": "={{ $json.url }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "Authorization", "value": "={{ $vars.CLICKUP_API_TOKEN }}"},
            {"name": "Content-Type", "value": "application/json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.body) }}",
        "options": {"response": {"response": {"responseFormat": "json"}}}}),
    # Fallback : Teams, GChat, etc. — la reponse est enregistree en PG meme si non livree
    node("Canal sans livraison directe", "n8n-nodes-base.noOp", 1, [3100, 680]),
    # Audit PG : enregistrer la reponse finale + ticket_events (pour tickets uniquement)
    pg_node("PG: audit reponse", [3560, 300], PG_AUDIT_SQL,
            "={{ [$('Router par canal').item.json.reply_text || '', "
            "$('Router par canal').item.json.ticket_id || '00000000-0000-0000-0000-000000000000', "
            "$('Router par canal').item.json.platform || '', "
            "$('Router par canal').item.json.autonomy_level || 'N0', "
            "true] }}"),

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

    # --- Branche Bot Admin (@itmg_admin_bot) ---
    sticky("noteAdmin",
           "## Bot Admin (@itmg_admin_bot)\n"
           "TelegramTrigger dedie au bot admin.\n"
           "Commandes : /tickets, /stats, /recents, /rapport, tickets par canal/priorite/departement.\n"
           "Reponses groupees par categorie, canal, priorite ou societe.\n"
           "Credential : Telegram Admin Bot (telegramAdminApi).",
           [-60, 940], w=520, h=140),
    node("AdminTrigger", "n8n-nodes-base.telegramTrigger", 1.2, [220, 1020], {
        "updates": ["message"],
        "additionalFields": {}},
        creds=({"telegramApi": CREDS["telegramAdminApi"]} if "telegramAdminApi" in CREDS else None)),
    node("Parser admin", "n8n-nodes-base.code", 2, [400, 1020],
         {"jsCode": ADMIN_PARSE_JS}),
    pg_node("PG: admin tickets", [600, 1020], PG_ADMIN_TICKETS_SQL, ""),
    node("Formater admin", "n8n-nodes-base.code", 2, [800, 1020],
         {"jsCode": ADMIN_FORMAT_JS}),
    node("HTTP: reponse admin", "n8n-nodes-base.httpRequest", 4.2, [1000, 1020], {
        "method": "POST",
        "url": "=https://api.telegram.org/bot{{ $vars.TELEGRAM_ADMIN_BOT_TOKEN }}/sendMessage",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": '={{ JSON.stringify({ chat_id: $json.chatId,'
                    ' text: $json.reply, parse_mode: "HTML" }) }}',
        "options": {"response": {"response": {"responseFormat": "json"}}},
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "Content-Type", "value": "application/json"}]}},
        on_error="continueRegularOutput"),
]

connections = merge_conn(
    conn([
        # --- Declencheurs → Normalisation ---
        ("TelegramTrigger", "Filtre Telegram (mention)"),
        ("Filtre Telegram (mention)", "Normaliser (multicanal)"),
        ("Webhook multicanal", "Normaliser (multicanal)"),
        ("Webhook Jira", "Normaliser (multicanal)"),
        ("Webhook Teams", "Normaliser (multicanal)"),
        ("Email IMAP (Outlook)", "Normaliser (multicanal)"),
        ("Declencheur manuel", "Normaliser (multicanal)"),
        # --- Vision ---
        ("Normaliser (multicanal)", "A une image ?"),
        ("A une image ?", "TG: getFile", 0),
        ("A une image ?", "PG: enregistrer evenement", 1),
        ("TG: getFile", "Preparer vision"),
        ("Preparer vision", "OpenRouter: vision"),
        ("OpenRouter: vision", "Fusionner texte image"),
        ("Fusionner texte image", "PG: enregistrer evenement"),
        # --- Contexte enrichi ---
        ("PG: enregistrer evenement", "PG: resoudre societe"),
        ("PG: resoudre societe", "PG: enrichir contexte"),
        ("PG: enrichir contexte", "Assembler le contexte"),
        # --- Agent Claude + garde-fous ---
        ("Assembler le contexte", "Agent Claude (triage)"),
        ("Agent Claude (triage)", "Parser + garde-fous"),
        ("Parser + garde-fous", "Est-ce un ticket ?"),
        # --- Non-ticket → detecter reporting ou repondre ---
        ("Est-ce un ticket ?", "Detecter reporting", 1),
        ("Detecter reporting", "Est-ce un reporting ?"),
        ("Est-ce un reporting ?", "PG: lister tickets", 0),
        ("Est-ce un reporting ?", "Preparer reponse (non-ticket)", 1),
        ("PG: lister tickets", "Formater rapport DM"),
        ("Formater rapport DM", "Router par canal"),
        ("Preparer reponse (non-ticket)", "Router par canal"),
        # --- Ticket → creer + router par niveau + notifier DEV MG ---
        ("Est-ce un ticket ?", "PG: creer le ticket", 0),
        ("PG: creer le ticket", "Router par niveau"),
        ("PG: creer le ticket", "Notifier nouveau ticket"),
        ("Notifier nouveau ticket", "TG: notif DEV MG"),
        ("Router par niveau", "N0 repondre", 0),
        ("Router par niveau", "N1 executer runbook", 1),
        ("Router par niveau", "N2 guider", 2),
        ("Router par niveau", "Preparer escalade N3", 3),
        # --- N0/N2 → reponse directe ---
        ("N0 repondre", "Preparer reponse"),
        ("N2 guider", "Preparer reponse"),
        # --- N1 → tenter execution runbook ---
        ("N1 executer runbook", "Runbook a webhook ?"),
        ("Runbook a webhook ?", "Appeler webhook runbook", 0),
        ("Runbook a webhook ?", "Preparer reponse", 1),
        ("Appeler webhook runbook", "Preparer reponse"),
        # --- N3 → escalade ClickUp + alerte Telegram DEV MG ---
        ("Preparer escalade N3", "ClickUp: creer tache"),
        ("ClickUp: creer tache", "Preparer alerte DEV MG"),
        ("Preparer alerte DEV MG", "Telegram: alerter techniciens"),
        ("Telegram: alerter techniciens", "Preparer reponse"),
        # --- Livraison multi-canal (ticket + non-ticket convergent ici) ---
        ("Preparer reponse", "Router par canal"),
        ("Router par canal", "Telegram: envoyer reponse", 0),
        ("Router par canal", "Email: repondre", 1),
        ("Router par canal", "Preparer commentaire Jira", 2),
        ("Router par canal", "Preparer commentaire ClickUp", 3),
        ("Router par canal", "Canal sans livraison directe", 4),
        ("Preparer commentaire Jira", "Jira: commenter"),
        ("Preparer commentaire ClickUp", "ClickUp: commenter"),
        # --- Audit PG : toutes les livraisons convergent ---
        ("Telegram: envoyer reponse", "PG: audit reponse"),
        ("Email: repondre", "PG: audit reponse"),
        ("Jira: commenter", "PG: audit reponse"),
        ("ClickUp: commenter", "PG: audit reponse"),
        ("Canal sans livraison directe", "PG: audit reponse"),
        # --- Reporting ---
        ("Webhook reporting", "PG: tickets en cours"),
        ("PG: tickets en cours", "Formater le rapport"),
        # --- Bot Admin (@itmg_admin_bot) ---
        ("AdminTrigger", "Parser admin"),
        ("Parser admin", "PG: admin tickets"),
        ("PG: admin tickets", "Formater admin"),
        ("Formater admin", "HTTP: reponse admin"),
    ]),
    conn([("Modele OpenRouter", "Agent Claude (triage)", 0, "ai_languageModel")]),
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
