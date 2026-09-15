# Les accès à réunir

Tout ce qu'il faut obtenir pour lancer le projet, dans l'ordre où ça devient
nécessaire. **Ne me transmettez rien par chat** : les secrets se saisissent
directement dans `.env` sur la VM ou dans le coffre de credentials n8n.

Rappel de conception : la base ne stocke jamais un secret, seulement un
`credential_ref`, c'est-à-dire le **nom** d'une credential n8n.

---

## Semaine 1 — indispensable pour démarrer

### 1. Le serveur et le domaine

Si les termes « VM », « VPS » ou « SSH » ne vous parlent pas, la procédure
complète — quoi commander, chez qui, et chaque commande à taper — est dans
[`docs/installation-vm.md`](docs/installation-vm.md).

| Élément | Détail |
|---|---|
| VM Linux | 4 vCPU, 8 Go de RAM, 60 Go de disque. Docker + plugin Compose |
| Accès SSH | Une clé, pas un mot de passe |
| Nom de domaine | Ex. `support.votredomaine.com`, enregistrement A vers l'IP de la VM |
| Ports ouverts | 80 et 443 entrants. **Le 80 est obligatoire** pour l'émission du certificat Let's Encrypt |

Le domaine doit résoudre **avant** le premier `docker compose up`, sinon Caddy
échoue à obtenir le certificat et n8n reste inaccessible.

### 2. Clé API Anthropic

`console.anthropic.com` → **API keys** → Create key.

- Poser un **plafond de dépense mensuel** et une alerte à 80 % dès la création
- Ordre de grandeur : 0,01 à 0,03 € par ticket traité
- Va dans `.env`, variable `ANTHROPIC_API_KEY`

### 3. Bot Telegram — le canal pilote

Parler à [@BotFather](https://t.me/BotFather) sur Telegram :

```
/newbot          → nom et @identifiant du bot
/setprivacy      → Disable
```

**`/setprivacy → Disable` est le point le plus souvent oublié.** Par défaut un
bot ne voit dans un groupe que les messages qui le mentionnent explicitement.
Tant que la confidentialité est activée, il ne recevra rien d'autre — ce qui
empêchera plus tard de passer en `trigger_mode = 'auto'`.

À me fournir, ou à saisir dans n8n :

| Élément | Comment l'obtenir |
|---|---|
| Jeton du bot | Donné par BotFather. Coffre n8n, credential `telegram_pilote` |
| ID du groupe de support | Ajouter le bot au groupe, puis lire `chat.id` dans les logs n8n. Négatif, ex. `-1001234567890` |
| ID du canal de validation | **Un groupe privé, réservé à l'équipe IT.** C'est là que partent les cartes à approuver |
| ID du canal d'escalade | Peut être le même que le précédent au démarrage |
| ID du canal d'administration | Nouveaux canaux détectés, alertes techniques, échecs |

Créez les trois canaux IT avant de commencer : ils ne doivent contenir que
l'équipe, jamais les demandeurs.

### 4. n8n

Rien à obtenir de l'extérieur. Trois valeurs à **générer** sur la VM :

```bash
openssl rand -base64 24                              # POSTGRES_PASSWORD
openssl rand -hex 32                                 # N8N_ENCRYPTION_KEY
docker run --rm caddy:2.8-alpine \
  caddy hash-password --plaintext 'votre-mot-de-passe'   # N8N_ADMIN_PASSWORD_HASH
```

**Conservez `N8N_ENCRYPTION_KEY` hors de la VM.** Sans elle, les credentials
d'une sauvegarde sont définitivement illisibles.

---

## Semaine 2 — pour que l'agent réponde (palier P2)

### 5. Confluence — la base de connaissances

| Élément | Où |
|---|---|
| URL de l'instance | `https://votreorg.atlassian.net/wiki` |
| Compte de service | Un compte dédié, **lecture seule** sur les espaces concernés |
| Jeton d'API | `id.atlassian.com` → Security → API tokens |
| Clé de l'espace | Ex. `ITSUPPORT`. Renseignée dans `tenant_policies.kb_space_key` |

Il faut aussi **20 à 30 articles réellement exploitables**. S'ils n'existent
pas, c'est la vraie tâche de la semaine 2 — et c'est au technicien référent de
les écrire, pas à moi : l'agent ne fera que restituer ce qu'il sait déjà.

### 6. L'historique du canal pilote

Le plus rentable de tout le projet, et ça ne coûte qu'un export.

Export Telegram Desktop → **JSON**, 2 à 3 mois. Sert au test à froid : rejouer
200 messages réels à travers l'agent et comparer sa classification à ce qui
s'est réellement passé, **avant** de déranger qui que ce soit.

C'est aussi ce qui permet de bâtir la taxonomie sur vos vrais mots plutôt que
sur mes suppositions — la partie la plus coûteuse à corriger après coup.

---

## Semaine 3 — pour que l'agent exécute (palier P3)

### 7. Jira Service Management

| Élément | Détail |
|---|---|
| URL | `https://votreorg.atlassian.net` |
| Compte de service | Droits de **création et mise à jour** sur le projet de service |
| Jeton d'API | Même endroit que Confluence |
| Clé du projet | Ex. `ITS`. Va dans `tenant_policies.ticket_project_key` |
| Types de demande | La correspondance catégorie → *request type* est à établir avec vous |

### 8. Les backends techniques

C'est ici que les accès deviennent sensibles. **Un compte de service dédié par
backend, avec des droits délégués minimaux. Jamais d'administrateur de domaine.**

**Active Directory sur site** — l'annuaire n'est pas joignable depuis n8n.
Trois options, par ordre de préférence :

1. Un micro-service interne sur un serveur joint au domaine, exposant
   `POST /users`, `POST /users/{id}/reset-password`. n8n l'appelle avec un
   jeton, en réseau interne uniquement. Propre, testable, droits isolés
2. n8n hébergé sur le réseau interne, appelant PowerShell via WinRM
3. Microsoft Graph — **seulement** si l'AD sur site n'est plus la source de
   vérité. En hybride, écrire dans le cloud est écrasé à la synchronisation
   suivante

Droits à déléguer : créer des utilisateurs **dans une unité d'organisation
précise**, réinitialiser les mots de passe d'un groupe précis. Rien d'autre.

**Microsoft 365 / Entra** — inscription d'application, permissions
*application* : `User.ReadWrite.All`, `Group.ReadWrite.All`,
`Mail.ReadWrite` selon les runbooks activés. Consentement administrateur requis.

**Google Workspace** — compte de service avec délégation à l'échelle du domaine,
scopes `admin.directory.user` et `admin.directory.group`.

**Coffre de secrets (Bitwarden, 1Password, Keeper)** — c'est la réponse au
problème des codes 2FA, et le meilleur retour sur investissement du projet.
Il faut une organisation d'équipe et un jeton d'API pour gérer les accès.

### 9. Boîte de support (si vous branchez le canal email)

IMAP en lecture + SMTP en envoi, sur un compte dédié. Pour Microsoft 365 et
Google, préférer OAuth2 à un mot de passe d'application.

---

## Plus tard — les autres plateformes

À n'ouvrir qu'une fois le socle validé sur Telegram. Ces deux-là demandent un
**consentement administrateur**, ce qui prend des semaines dans la plupart des
organisations : lancez les démarches tôt, même si le branchement est loin.

**Microsoft Teams** — inscription d'application Azure Bot, Teams App manifest,
endpoint de messagerie HTTPS, permissions `ChannelMessage.Read.All`.

**Google Chat** — projet Google Cloud, Chat API activée, configuration de l'app
avec l'URL du webhook, compte de service.

---

## Récapitulatif

| # | Accès | Quand | Bloquant |
|---|---|---|---|
| 1 | VM + domaine + ports 80/443 | Semaine 1 | **oui** |
| 2 | Clé API Anthropic | Semaine 1 | **oui** |
| 3 | Bot Telegram + 4 identifiants de canaux | Semaine 1 | **oui** |
| 4 | Secrets générés sur la VM | Semaine 1 | **oui** |
| 5 | Confluence + 20-30 articles | Semaine 2 | pour le palier P2 |
| 6 | Export de l'historique du groupe | Semaine 2 | pour le test à froid |
| 7 | Jira Service Management | Semaine 3 | non, `ticket_backend = 'none'` suffit |
| 8 | Backends techniques | Semaine 3 | pour le palier P3 |
| 9 | Boîte de support IMAP/SMTP | Plus tard | non |
| 10 | Teams, Google Chat | Plus tard | non |

**Les quatre premiers suffisent pour démarrer.** Avec eux seuls, l'agent trie,
crée les tickets, escalade et accuse réception dès le premier jour — palier P1.
Tout le reste s'ajoute sans redéploiement.
