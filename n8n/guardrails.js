// Garde-fous appliques APRES la reponse du modele (regle 5 du document).
// Porte depuis engine/guardrails.py. Aucune dependance.
function checkForbidden(text) {
  text = (text || '').toLowerCase();
  const P = [
    ['transmission de secret', /\b(2fa|otp|code de v[eé]rification|mot de passe|password|token|mfa)\b.*\b(donn\w*|envoi\w*|transmet\w*|transmett\w*|partag\w*|besoin|c'est quoi|quel est)\b|\b(donn\w*|envoi\w*|transmet\w*|transmett\w*|partag\w*)\b.*\b(2fa|otp|mot de passe|password|token|mfa)\b/],
    ['compte a privileges', /\b(domain admins?|enterprise admins?|schema admins?|global admin|root|sudo|administrateur du domaine|admin du domaine)\b/],
    ['suppression de donnees', /\b(supprim\w*|efface\w*|delete|purge\w*)\b.*\b(compte|bo[iî]te|mail|donn[ée]es|fichier|utilisateur)\b/],
    ['modification de regle de securite', /\b(pare-feu|firewall|politique mfa|conditional access|antivirus|gpo)\b/],
    ['donnees RH ou financieres', /\b(paie|salaire|bulletin|rh\b|contrat de travail|facture|comptabilit[ée])\b/],
    ['action sur la production', /\b(production|prod\b|serveur de prod)\b/],
    ['prise en main a distance', /\b(rdp|bureau [àa] distance|anydesk|teamviewer|prise en main)\b/],
  ];
  for (const [reason, re] of P) if (re.test(text)) return reason;
  return null;
}

const ORDER = { N0:0, N1:1, N2:2, N3:3 };
const ALLOWED_BY_CAP = { N3:['N0','N1','N2','N3'], N2:['N0','N2','N3'], N1:['N0','N1','N3'], N0:['N0','N3'] };

function downgrade(t, adj, reason) {
  if (t.autonomy_level !== 'N3') adj.push('N3 force : ' + reason + ' (etait ' + t.autonomy_level + ')');
  t.autonomy_level = 'N3';
  if (!t.escalation_reason) t.escalation_reason = reason;
}

// ctx = { max_autonomy, observation_only, identity:{verified,role,email},
//         runbooks:{CODE:{autonomy_level,allowed_roles:[],manager_approval}},
//         taxonomy_categories:[], confidence_floor }
function applyGuardrails(raw, ctx) {
  const t = Object.assign({}, raw);
  const adjustments = [], alerts = [];
  const floor = ctx.confidence_floor == null ? 0.7 : ctx.confidence_floor;

  if (t.alert) alerts.push(String(t.alert));
  if (t.is_ticket === false) return { triage: t, adjustments, alerts };

  if (!(t.autonomy_level in ORDER)) {
    downgrade(t, adjustments, "niveau invalide '" + t.autonomy_level + "'");
    alerts.push('niveau invalide renvoye par le modele : ' + t.autonomy_level);
  }

  const forbidden = checkForbidden((t.title || '') + ' ' + (t.summary || ''));
  if (forbidden) { downgrade(t, adjustments, 'interdit permanent : ' + forbidden); alerts.push('interdit permanent : ' + forbidden); }

  if (typeof t.confidence === 'number' && t.confidence < floor)
    downgrade(t, adjustments, 'confiance ' + t.confidence + ' < ' + floor);

  const cats = ctx.taxonomy_categories || [];
  if (cats.length && cats.indexOf(t.category) === -1) {
    const bad = t.category; t.category = 'information'; t.subcategory = 'question_procedure';
    adjustments.push("categorie hors taxonomie '" + bad + "' -> information");
    alerts.push('categorie inconnue : ' + bad);
  }

  const allowed = ALLOWED_BY_CAP[ctx.max_autonomy] || ['N3'];
  if (allowed.indexOf(t.autonomy_level) === -1)
    downgrade(t, adjustments, 'plafond societe ' + ctx.max_autonomy);

  const id = ctx.identity || {};
  if (!id.verified && t.autonomy_level === 'N1')
    downgrade(t, adjustments, 'demandeur non verifie, N1 interdit');

  if (t.autonomy_level === 'N1') {
    const rb = (ctx.runbooks || {})[t.runbook_code];
    if (!rb) { downgrade(t, adjustments, "runbook '" + t.runbook_code + "' inconnu"); alerts.push('runbook inconnu : ' + t.runbook_code); }
    else if (rb.autonomy_level !== 'N1') downgrade(t, adjustments, "runbook '" + t.runbook_code + "' non N1");
    else if ((rb.allowed_roles || []).indexOf(id.role) === -1) downgrade(t, adjustments, "role '" + id.role + "' hors allowed_roles");
    else {
      const ben = t.beneficiaire_email || (t.runbook_params || {}).beneficiaire_email;
      if (ben && id.email && ben.toLowerCase() !== id.email.toLowerCase()
          && ['manager','it','admin'].indexOf(id.role) === -1)
        downgrade(t, adjustments, 'action pour un tiers sans role manager/it/admin');
      else if (rb.manager_approval) t._manager_approval_required = true;
    }
  }

  t._publish = ctx.observation_only ? false : true;
  if (ctx.observation_only) adjustments.push('palier P0 : ticket cree, aucune publication');
  return { triage: t, adjustments, alerts };
}
module.exports = { applyGuardrails, checkForbidden };
