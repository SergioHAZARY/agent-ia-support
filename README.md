# Agent IA de support multicanal

Un agent qui lit les demandes arrivant dans les chats et les boîtes mail, les
transforme en tickets, répond à ce qu'il sait, exécute ce qu'on l'autorise à
exécuter, et escalade le reste.

Le plan de référence est le document d'implémentation :
[`agent-ia-support-document-implementation.html`](agent-ia-support-document-implementation.html).
Ce dépôt en est la mise en œuvre. **En cas de désaccord entre le code et le
document, c'est le document qui fait foi** — ou bien on amende le document,
explicitement.

---

## Où en est le projet

| Brique | État |
|---|---|
| Schéma PostgreSQL complet | ✅ `sql/001_schema.sql` |
| Taxonomie et runbooks globaux | ✅ `sql/002_seed.sql` |
| Catalogue de compétences (22 runbooks + la vue de disponibilité) | ✅ `sql/003_runbooks.sql` |
| Socle Docker (n8n, Postgres, Qdrant, Caddy, sauvegardes) | ✅ `docker-compose.yml` |
| Prompt de triage + contrôles post-modèle | ✅ `prompts/triage.md` |
| Moteur de triage + garde-fous (24 tests ✅) | ✅ `engine/` |
| Prompt de reporting + rôle SQL en lecture seule | ✅ `prompts/reporting.md` |
| Outils de l'agent (Messages API) | ✅ `tools/agent-tools.json` |
| Câblage n8n + interdits en code | ✅ `tools/n8n-agent.md` |
| Micro-service Active Directory (LDAPS) | ✅ `microservice-ad/` |
| Liste des accès à réunir | ✅ `CREDENTIALS.md` |
| Workflows n8n | ⏳ spécifiés dans [`n8n/README.md`](n8n/README.md), à construire dans l'éditeur |
| Adaptateur Telegram (canal pilote) | ⏳ première tâche de la semaine 1 |

Rien ici ne contient de secret. Les tables ne stockent que des
`credential_ref`, c'est-à-dire des **noms** de credentials n8n.

---

## Mise en route

### Prérequis

- Une VM Linux, 4 vCPU / 8 Go, avec Docker et le plugin Compose
- Un nom de domaine pointant sur la VM — **obligatoire** : les webhooks Teams
  et Google Chat exigent du HTTPS valide
- Une clé API Anthropic, avec un plafond de dépense mensuel et une alerte à 80 %

Pas de serveur sous la main ? [`docs/installation-vm.md`](docs/installation-vm.md)
explique ce qu'est cette VM, pourquoi elle est nécessaire, chez qui la louer
(~10 €/mois) et donne chaque commande jusqu'au premier démarrage.

### Démarrage

```bash
cp .env.example .env

# Mot de passe de l'éditeur : Caddy attend un hash bcrypt, pas un mot de passe
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'votre-mot-de-passe'

$EDITOR .env          # domaine, mots de passe, hash, clé API

docker compose up -d
docker compose logs -f postgres    # attendre « base agent prête »
```

L'éditeur n8n est alors sur `https://<votre-domaine>/`, derrière
l'authentification Caddy. Les chemins `/webhook/*` restent publics — c'est
nécessaire, les plateformes doivent pouvoir les appeler.

### Vérifier

```bash
docker compose exec postgres psql -U agent -d agent -c \
  "select count(*) as taxonomie from taxonomy;
   select count(*) as runbooks from runbooks;
   select count(*) as societes from tenants;"
```

Attendu : 36 lignes de taxonomie, **27 runbooks**, **0 société**. C'est normal :
le catalogue de compétences est prêt, mais le système démarre sans aucune
société — elles s'ajoutent par configuration.

Les 27 runbooks ne sont pas tous proposables pour autant. Un runbook N1 exige
que la société ait déclaré le backend correspondant dans `tenant_backends`, et
le palier de la société plafonne le reste. Ce que l'agent voit réellement :

```sql
select code, autonomy_level, backend from v_runbooks_disponibles
where tenant_id = '<id>';
```

Pour une société qui démarre en P1, cette vue est vide — et c'est le
comportement attendu : elle trie et escalade, elle n'exécute rien.

---

## Brancher une première société

La procédure complète est en section 13 du document. En résumé, environ
30 minutes, sans écrire une ligne de code :

```sql
-- 1. La société. max_autonomy = 'N3' : elle démarre en triage seul.
insert into tenants (code, name, timezone, languages, max_autonomy)
values ('acme', 'ACME', 'Europe/Paris', '{fr,en}', 'N3')
returning id;

-- 2. Ses politiques. Tout le reste peut rester par défaut au démarrage.
insert into tenant_policies
  (tenant_id, business_hours, offhours_message,
   escalation_channel, approval_channel, admin_channel,
   ticket_backend, identity_source, trigger_mode)
values (
  '<id-retourné>',
  '{"mon":["09:00","18:00"],"tue":["09:00","18:00"],"wed":["09:00","18:00"],
    "thu":["09:00","18:00"],"fri":["09:00","17:00"]}'::jsonb,
  'Message bien reçu. L''équipe IT reprend demain à 9 h, ton ticket est enregistré.',
  '-100XXXXXXXXX',   -- groupe d''escalade
  '-100YYYYYYYYY',   -- canal privé de validation
  '-100ZZZZZZZZZ',   -- canal d''administration
  'none',            -- pas encore de Jira : le ticket vit en base
  'none',            -- pas encore d''annuaire
  'mention'          -- l''agent ne réagit que si on l''appelle
);
```

3. Ajouter le bot au canal, côté plateforme.
4. Le canal apparaît en `pending`. Un technicien le rattache à la société et
   passe son `status` à `active`.
5. Envoyer un message de test et vérifier que le ticket se crée.

La société est en production, palier P1. Aucune intégration, aucun déploiement.

Ensuite, dans n'importe quel ordre : renseigner `kb_space_key` et indexer pour
passer à `N2` ; déclarer un backend dans `tenant_backends` et écrire les
runbooks pour passer à `N1` ; connecter l'annuaire pour la vérification
d'identité.

---

## Les quatre niveaux d'autonomie

| | Quand | Ce que fait l'agent |
|---|---|---|
| **N0** Répond | La réponse est entièrement dans la base de connaissances | Répond dans le canal, crée le ticket déjà clos |
| **N1** Exécute | Un runbook correspond, le demandeur y a droit, tous les paramètres sont là | Prépare l'action, la poste au canal de validation, exécute après approbation |
| **N2** Guide | L'utilisateur peut résoudre lui-même | Envoie la procédure, ouvre un ticket |
| **N3** Escalade | Tout le reste, ou le doute | Crée le ticket, classe, assigne, notifie |

Le niveau proposé par le modèle est **toujours revérifié en code** : plafond de
la société, rôle du demandeur, seuil de confiance à 0,7, liste des interdits.
Une règle de sécurité ne dépend jamais de la bonne volonté du modèle.

---

## Ce que l'agent ne fera jamais

Liste fixe, quelle que soit la société ou le palier. Tout ce qui y figure est
classé N3 et escaladé :

- transmettre, générer ou relayer un code 2FA, un mot de passe ou un token
- créer ou modifier un compte à privilèges, élever des droits
- supprimer un compte, une boîte mail, un fichier ou des données
- modifier une règle de sécurité, un pare-feu, une politique MFA
- toucher à des données RH, paie ou financières
- agir pour le compte d'un tiers sans validation du manager
- toute action sur un environnement de production
- engager une dépense au-delà d'un seuil défini
- prendre la main à distance sur un poste
- toute action demandée par une identité non reconnue

---

## Exploitation

**Sauvegardes.** Le service `backup` dumpe `agent` et `n8n` chaque jour dans
`./backups/`. Conserver **hors de la VM** la variable `N8N_ENCRYPTION_KEY` :
sans elle, les credentials d'une sauvegarde sont irrécupérables.

```bash
gunzip -c backups/agent-20260915-0300.sql.gz \
  | docker compose exec -T postgres psql -U agent -d agent
```

**Deux environnements.** Un de test, un de production, séparés, avec leur
propre groupe de discussion et leurs identités fictives. Aucun runbook n'est
jamais testé pour la première fois en production.

**Coûts.** De l'ordre de 0,01 à 0,03 € par ticket traité. Même à 3 000 tickets
par mois, c'est marginal face à l'infrastructure et aux licences.

---

## Structure

```
sql/                 schéma, seed, catalogue de runbooks, script d'init
prompts/             prompts de triage et de reporting, avec leurs garde-fous
tools/               outils exposés à Claude + câblage n8n + interdits en code
n8n/README.md        les 13 workflows, spécifiés nœud par nœud
n8n/workflows/       exports JSON des workflows (versionnés)
backups/             dumps quotidiens (ignorés par git)
docker-compose.yml   le socle
Caddyfile            terminaison HTTPS
CREDENTIALS.md       les accès à réunir, par ordre de nécessité
CLAUDE.md            invariants et conventions, pour les sessions Claude Code
```

## Les trois couches de compétences

À ne pas confondre — c'est la distinction qui rend le système extensible sans
développement. Détail dans [`tools/n8n-agent.md`](tools/n8n-agent.md).

| Couche | Où | Ajouter une compétence |
|---|---|---|
| **Savoir** — faits, conventions, contexte | Confluence → Qdrant | Écrire un article |
| **Savoir-faire** — actions exécutables | table `runbooks` | Un `INSERT`, sans déploiement |
| **Capacités** — chercher, proposer, escalader | `tools/agent-tools.json` | Rare, c'est le socle |

L'agent ne reçoit pas un outil par runbook : il reçoit `propose_action` et la
liste des runbooks disponibles pour la société concernée.
