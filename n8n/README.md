# Les workflows n8n

Spécification nœud par nœud. Les workflows se construisent dans l'éditeur, puis
s'exportent en JSON dans `n8n/workflows/` — **versionnés dès le premier jour**,
c'est la seule façon de relire un changement de comportement.

Principe directeur : **les adaptateurs ne connaissent aucune société.** Un
adaptateur par plateforme, jamais par société. Quinze groupes Telegram passent
par le même workflow.

| Code | Nom | Rôle | Priorité |
|---|---|---|---|
| `WF-00` | Découverte de canal | Enregistre un canal inconnu en `pending`, notifie l'administration | Semaine 1 |
| `WF-01t` | Adaptateur Telegram | Reçoit l'évènement, extrait les champs bruts | Semaine 1 |
| `WF-01x` | Adaptateurs Teams / Google Chat / email | Idem, une fois le socle validé | Plus tard |
| `WF-02` | Contexte | Résout canal → société, charge tout le contexte | Semaine 1 |
| `WF-03` | Triage | Appelle Claude, applique les plafonds et les interdits | Semaine 2 |
| `WF-04` | Validation | Poste la carte de validation, attend le clic | Semaine 3 |
| `WF-05` | Exécution | Appelle le webhook du runbook validé | Semaine 3 |
| `WF-06` | Réponse | Publie dans le canal d'origine selon la plateforme | Semaine 1 |
| `WF-07` | Ticket | Crée ou met à jour dans Jira ou ClickUp | Semaine 3 |
| `WF-08` | Indexation KB | Nocturne : Confluence → Qdrant | Semaine 2 |
| `WF-09` | Synchro identités | Nocturne : annuaire → `identities` | Quand un annuaire existe |
| `WF-10` | Apprentissage | À la clôture d'un N3, propose un brouillon de runbook | Après le pilote |
| `WF-11` | Reporting | Agent en lecture seule sur la base | Semaine 4 |

---

## WF-01t — Adaptateur Telegram

Le seul workflow à écrire pour ouvrir le canal pilote.

```
Telegram Trigger  (updates: message, callback_query)
  │
  ├─ IF  callback_query présent ──────────→ WF-04 (clic sur une carte)
  │
  ▼
Function « normaliser »
    platform          = 'telegram'
    external_id       = {{$json.message.chat.id}}
    source_message_id = {{$json.message.message_id}}
    sender_external   = {{$json.message.from.id}}
    sender_raw        = prénom + nom + @username
    body              = {{$json.message.text || $json.message.caption}}
    attachments       = photo / document, en références, pas en binaire
  │
  ▼
Postgres « résoudre le canal »
    select c.*, t.code, t.max_autonomy
    from channels c left join tenants t on t.id = c.tenant_id
    where c.platform = $1 and c.external_id = $2
  │
  ├─ aucune ligne ──────────────────────→ WF-00, puis STOP
  ├─ status <> 'active' ────────────────→ STOP silencieux
  ├─ trigger_mode = 'mention'
  │     et pas de @mention du bot
  │     et pas une réponse à un message du bot ─→ STOP silencieux
  ▼
Postgres « insérer l'évènement »
    insert into events (...) values (...)
    on conflict (channel_id, source_message_id) do nothing
    returning id
  │
  ├─ rien retourné (doublon) ───────────→ STOP
  ▼
Execute Workflow → WF-02 (contexte)
```

Les étapes « résoudre le canal » à « insérer l'évènement » sont **identiques
pour toutes les plateformes**. Les extraire en sous-workflow dès le deuxième
adaptateur : concrètement, quatre petits adaptateurs et un gros workflow commun.

### Points d'attention

- **Enregistrer avant d'interpréter.** L'insertion dans `events` précède tout
  appel au modèle. Si le triage tombe, on rejoue la table, rien n'est perdu.
- **La déduplication est en base**, pas en mémoire : Telegram peut relivrer le
  même update après un timeout.
- `getUpdates` (polling) suffit pour démarrer et évite d'attendre le certificat.
  Basculer en webhook ensuite.

---

## WF-02 — Contexte

Sous-workflow commun, appelé par tous les adaptateurs. Ne parle jamais à Claude.

1. Charger `tenants` + `tenant_policies` + `tenant_backends`
2. Résoudre l'identité : `identities` par `telegram_user_id` / `teams_user_id` /
   `google_user_id`. Absente → créer avec `verified = false`
3. Rattachement de conversation : ticket existant si **même demandeur, même
   canal, non clos, moins de 4 heures**. Sinon nouveau ticket
4. Charger l'historique récent du demandeur (5 derniers échanges)
5. Recherche Qdrant dans la collection `kb`, filtrée sur `tenant_id` → 5 extraits
6. Recherche Qdrant dans la collection `cas_resolus`, filtrée sur `tenant_id`
   → 3 cas, avec leur `resolution_note`
7. Charger les runbooks actifs : ceux de la société **et** les globaux
   (`tenant_id is null`), le spécifique écrasant le global à code égal
8. Assembler le prompt depuis `prompts/triage.md`

Le filtrage des runbooks par société n'est pas cosmétique : sans lui, l'agent
proposera une procédure Google Workspace à un utilisateur Active Directory.

---

## WF-03 — Triage

1. Appel Claude (modèle `$env.ANTHROPIC_MODEL`), sortie JSON contrainte
2. **Parsing défensif** : JSON invalide → N3, alerte administration
3. Appliquer les contrôles du tableau « Vérifications appliquées APRÈS la
   réponse » de [`../prompts/triage.md`](../prompts/triage.md). En code, pas
   dans le prompt
4. `is_ticket = false` → journaliser dans `events`, ne rien créer, STOP
5. Écrire le ticket, journaliser `triage` dans `ticket_events`
6. Router : N0 → WF-06 · N1 → WF-04 · N2 → WF-06 · N3 → escalade + WF-06

Sur trois tentatives en échec (Claude indisponible) : mise en file de reprise,
puis alerte dans le canal d'administration au bout de 15 minutes.

---

## WF-04 — Validation

Poste la carte dans `approval_channel`, avec trois boutons :

```
Ticket ITS-1247 · N1 · Confiance 0,88
Demandeur : R. H. (employé) · vérifié
Demande   : ajout à une licence

Action proposée : ADD_SEAT
  email = prenom.nom@societe.com

Réponse proposée :
"Bonjour, ton accès est activé. Connecte-toi avec ton mail professionnel,
 tu recevras le lien d'invitation dans 2 minutes."

[ Approuver ]  [ Modifier ]  [ Rejeter ]
```

Le bouton **Modifier** est le plus important des trois : c'est lui qui fournit
la matière pour améliorer le prompt et la base de connaissances. La version
corrigée est enregistrée dans `final_response`, à côté de `ai_response`.
L'écart entre les deux est la mesure principale de la qualité de l'agent.

Chaque clic est journalisé dans `ticket_events` avec le nom du technicien.

---

## WF-05 — Exécution

1. Relire le runbook en base et **revérifier** `allowed_roles`, `max_autonomy`,
   les interdits. Ne jamais faire confiance au contenu de la carte : elle a pu
   attendre plusieurs heures, les droits ont pu changer
2. Valider les paramètres contre `params_schema`
3. Appeler `n8n_webhook` avec la credential déclarée dans `tenant_backends`
4. Journaliser `execution` ou `echec`
5. Échec → notifier le canal d'administration, ne jamais réessayer en silence
   une action à effet de bord

---

## WF-08 — Indexation de la base de connaissances

Nocturne. Lit l'espace Confluence de chaque société ayant `kb_source =
'confluence'`, découpe en passages, calcule les embeddings, écrit dans la
collection `kb` de Qdrant avec `tenant_id` en payload.

**Le multilingue est un piège** : un article rédigé en français ne sera pas
retrouvé par une question posée en arabe. Indexer chaque article dans les
langues déclarées dans `tenants.languages`, et suivre le taux d'escalade par
langue.

---

## Le test à froid — semaine 2

L'étape la plus rentable du projet, et la seule occasion de corriger la
taxonomie avant qu'elle ne coûte cher.

1. Exporter 2 à 3 mois d'historique du canal pilote
2. Rejouer les 200 derniers messages à travers WF-02 et WF-03, **sans WF-06** :
   l'agent classe, il ne répond à personne
3. Comparer sa classification à ce qui s'est réellement passé

Critères pour ouvrir les réponses automatiques : 85 % de classification
correcte, 70 % des réponses proposées envoyées sans modification majeure, zéro
incident de sécurité, au moins 20 runbooks écrits.

---

## Gestion des échecs — s'applique à tous les workflows

Chaque appel externe (Claude, Jira, backend technique) :

1. trois tentatives avec attente croissante
2. puis mise en file de reprise
3. puis alerte dans le canal d'administration au bout de 15 minutes

Un message entrant n'est jamais perdu : il est dans `events` avant tout
traitement, et la table se rejoue.
