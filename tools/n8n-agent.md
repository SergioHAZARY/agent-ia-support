# Câbler l'agent dans n8n

Comment les huit outils de [`agent-tools.json`](agent-tools.json) deviennent des
nœuds n8n, et où passent les contrôles de sécurité.

---

## Les trois couches, à ne pas confondre

C'est la distinction qui structure tout le reste. Une même règle métier se
retrouve aux trois endroits, mais pour des raisons différentes.

| Couche | Où elle vit | Ce qu'elle porte | Qui la modifie |
|---|---|---|---|
| **Savoir** | Confluence → Qdrant | Les faits, les conventions, le contexte. L'agent le *lit*. | Le technicien référent, en rédigeant des articles |
| **Savoir-faire** | table `runbooks` | Les actions : paramètres, conditions, webhook. L'agent les *choisit*. | Un `INSERT`, sans déploiement |
| **Capacités** | `agent-tools.json` | Ce que l'agent peut faire *structurellement* : chercher, proposer, escalader | Rarement — c'est le socle |

L'agent ne reçoit **pas** un outil par runbook. Il reçoit `propose_action` et la
liste des runbooks disponibles pour la société, dans son contexte. Ajouter une
compétence métier n'est donc jamais une modification de code.

---

## Deux architectures possibles dans n8n

### A. Chaîne déterministe — recommandée pour démarrer

Un nœud **HTTP Request** appelle l'API Anthropic, n8n lit la réponse et route.
Le modèle produit un JSON de triage unique, il n'appelle pas d'outil lui-même.

C'est l'architecture décrite dans le document (WF-03), et c'est la bonne pour le
pilote : un seul appel, coût prévisible, comportement reproductible, et chaque
branche est visible dans l'éditeur. Le contexte (KB, cas similaires, runbooks)
est assemblé *avant* l'appel, par WF-02.

### B. Nœud AI Agent — quand le diagnostic demande plusieurs tours

Le nœud **AI Agent** de n8n, avec un **Anthropic Chat Model** et des **Tool
Workflow** branchés. Le modèle décide lui-même quels outils appeler et boucle.

Plus puissant pour les demandes qui demandent d'aller chercher plusieurs
informations avant de conclure. En contrepartie : coût variable, nombre de tours
à plafonner, et il faut se discipliner pour que les garde-fous ne partent pas
dans le prompt.

**Recommandation : commencer par A.** Passer les catégories qui le méritent en B
une fois la taxonomie stabilisée, pas avant.

---

## Correspondance outil → sous-workflow

Chaque outil est un **Tool Workflow** pointant sur un sous-workflow n8n. Le
`tenant_id` et l'`identity_id` ne sont **jamais** des paramètres que le modèle
remplit : ils sont injectés par WF-02 depuis le canal d'origine. C'est ce qui
empêche une demande de lire les données d'une autre société.

| Outil | Sous-workflow | Ce qu'il fait | Effet de bord |
|---|---|---|---|
| `search_kb` | `TOOL-kb` | Embedding de la question → Qdrant, collection `kb`, filtre `tenant_id` + `langue` | non |
| `find_similar_tickets` | `TOOL-cas` | Qdrant, collection `cas_resolus`, filtre `tenant_id` | non |
| `lookup_identity` | `TOOL-identity` | `select` sur `identities`, colonnes non sensibles uniquement | non |
| `propose_action` | `WF-04` | Valide contre `params_schema`, poste la carte de validation | **oui, différé** |
| `request_information` | `WF-06` | Publie la demande, passe le ticket en `en_attente_demandeur` | oui |
| `escalate` | `WF-06` + escalade | Crée/route le ticket, notifie le canal d'escalade | oui |
| `reply_to_requester` | `WF-06` | Publie dans le canal d'origine | oui |
| `query_tickets` | `WF-11` | `select` via le rôle `reporting_ro`, filtré sur le tenant | non |

---

## Les contrôles, et où ils s'exécutent

Le modèle propose, le code dispose. Aucun de ces contrôles n'est dans le prompt —
ils y sont *rappelés*, ce qui n'est pas la même chose.

### À l'entrée de `propose_action` (WF-04)

```
1. runbook_code figure-t-il dans v_runbooks_disponibles pour CE tenant ?
      non  → rejet, alerte administration (le modèle a inventé un code)
2. params valides contre params_schema ?
      non  → rejet, demande de complément
3. rôle du demandeur ∈ allowed_roles ?
      non  → rabaissé en N3
4. beneficiaire_email ≠ demandeur, et rôle ∉ {manager, it, admin} ?
      oui  → rabaissé en N3
5. manager_approval = true ?
      oui  → la carte exige la validation du manager, pas seulement d'un technicien
6. le runbook touche-t-il un interdit permanent ?
      oui  → rejet sec, alerte
```

### À la sortie de l'appel au modèle (WF-03)

```
confidence < 0.7                        → N3
autonomy_level > tenants.max_autonomy   → N3
tenants.observation_only = true         → ticket créé, WF-06 ne publie rien
demandeur non vérifié et niveau N1      → N3
category hors taxonomie                 → information/question_procedure + alerte
alert non nul                           → notification du canal d'administration
```

### Au moment de l'exécution (WF-05), après le clic

**Tout est revérifié.** Une carte peut attendre des heures ; entre la proposition
et l'approbation, un rôle a pu changer, un collaborateur a pu partir, un backend
a pu être désactivé. Ne jamais faire confiance au contenu de la carte.

---

## Les interdits, en code

À écrire une fois, dans un nœud Function appelé par WF-03 **et** WF-05. Cette
liste ne dépend d'aucune société et d'aucun palier.

```js
// Retourne une raison de blocage, ou null si la demande est recevable.
function verifierInterdits(triage, contexte) {
  const texte = `${triage.title} ${triage.summary}`.toLowerCase();

  // 1. Secrets — jamais transmis, dans aucun canal, même privé.
  if (/\b(2fa|otp|code de v[eé]rification|mot de passe|password|token|mfa)\b/.test(texte)
      && /\b(donne|envoie|transmet|partage|c'est quoi|quel est)\b/.test(texte)) {
    return 'transmission de secret';
  }

  // 2. Comptes à privilèges et élévation de droits.
  if (/\b(domain admin|enterprise admin|schema admin|global admin|root|sudo|administrateur du domaine)\b/.test(texte)) {
    return 'compte à privilèges';
  }

  // 3. Suppressions — l'agent désactive, il ne supprime jamais.
  if (/\b(supprim|efface|delete|purge)\w*\b/.test(texte)
      && /\b(compte|bo[iî]te|mail|donn[ée]es|fichier|utilisateur)\b/.test(texte)) {
    return 'suppression de données';
  }

  // 4. Règles de sécurité.
  if (/\b(pare-feu|firewall|politique mfa|conditional access|antivirus|gpo)\b/.test(texte)) {
    return 'modification de règle de sécurité';
  }

  // 5. Données RH, paie, finance.
  if (/\b(paie|salaire|bulletin|rh\b|contrat de travail|facture|comptabilit[ée])\b/.test(texte)) {
    return 'données RH ou financières';
  }

  // 6. Production.
  if (/\b(production|prod\b|serveur de prod)\b/.test(texte)) {
    return 'action sur la production';
  }

  // 7. Prise en main à distance.
  if (/\b(rdp|bureau [àa] distance|anydesk|teamviewer|prise en main)\b/.test(texte)) {
    return 'prise en main à distance';
  }

  // 8. Identité non reconnue — le contrôle le plus important.
  if (!contexte.identite || !contexte.identite.verified) {
    if (triage.autonomy_level === 'N1') return 'identité non vérifiée';
  }

  return null;
}
```

Ces expressions régulières sont un **filet**, pas la protection principale. La
protection principale, ce sont `allowed_roles`, `v_runbooks_disponibles` et la
validation humaine. Un filtre par mots-clés se contourne ; une vérification de
rôle en base, non.

---

## Le cas des codes 2FA

C'est la demande la plus fréquente dans les groupes de support, et la seule qui
présente un risque réel. Le catalogue y répond sans jamais transmettre de code.

| Situation | Ce que fait l'agent |
|---|---|
| Compte **partagé**, demandeur autorisé | `reply_to_requester` en **privé** : indique dans quel coffre se trouve le secret, jamais son contenu |
| Compte **partagé**, demandeur sans accès | `propose_action` → `VAULT_GRANT_ACCESS`, validation manager. La personne lira le code elle-même |
| Compte **individuel**, MFA à réinitialiser | `escalate` en N3. Vérification d'identité hors canal obligatoire — c'est le scénario d'ingénierie sociale le plus courant |
| Demande de **transmettre** un code | Bloqué par `verifierInterdits`. N3 + `alerte_securite: true` |

Aucune branche ne mène à « l'agent envoie le code ». C'est volontaire :
automatiser cette pratique l'industrialiserait.

---

## Ajouter une compétence

Le test de validité de l'architecture. Si ça dépasse ces trois lignes, quelque
chose est mal conçu.

**Une procédure que l'agent explique (N2)** — un `INSERT` dans `runbooks`, plus
un article dans la base de connaissances. Aucun déploiement.

**Une action que l'agent exécute (N1)** — un sous-workflow n8n exposé en webhook,
puis un `INSERT` dans `runbooks` avec son `backend`, son `params_schema` et ses
`allowed_roles`. Elle n'apparaîtra que pour les sociétés ayant déclaré ce backend.

**Une capacité structurelle** — un outil de plus dans `agent-tools.json` et son
sous-workflow. C'est rare, et ça se réfléchit : chaque outil ajouté élargit la
surface de ce que le modèle peut décider seul.
