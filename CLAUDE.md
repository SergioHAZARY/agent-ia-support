# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Nature du dépôt

Deux couches qui doivent rester cohérentes : un **document de conception** qui fait autorité, et le **socle d'exécution** qui le met en œuvre.

| Chemin | Rôle |
|---|---|
| `agent-ia-support-document-implementation.html` | **Source de vérité.** Document v1.1, 17 sections + 2 annexes. Autonome (CSS et SVG inline). |
| `agent-ia-support-artifact.html` | Version publiée du même document (artifact claude.ai). Générée depuis le source de l'artifact, pas depuis le fichier ci-dessus. |
| `agent-ia-support-document-implementation1.docx` | Export pour diffusion. Régénéré depuis le HTML, jamais édité directement. **Encore en v1.0.** |
| `docker-compose.yml`, `Caddyfile`, `.env.example` | Le socle : n8n, PostgreSQL, Qdrant, Caddy, sauvegardes |
| `sql/` | `init.sh` + fichiers numérotés : `001_schema.sql`, `002_seed.sql`, `003_runbooks.sql`, `004_reporting_views.sql` |
| `prompts/` | Prompts de triage et de reporting, avec leurs garde-fous post-modèle |
| `engine/` | Moteur de triage Python (`triage.py`, `guardrails.py`) — même logique que `n8n/guardrails.js`, mais testable sans Docker ni clé API |
| `microservice-ad/` | Seul composant qui parle au contrôleur de domaine `FSAGET.PRI` en LDAPS ; s'installe sur le bastion, jamais sur le serveur n8n |
| `tools/` | `agent-tools.json` (les outils exposés au modèle) et `n8n-agent.md` (câblage outil → sous-workflow, interdits en code) |
| `skills/support-agent/` | Skill du projet Claude de pilotage (claude.ai) — reporting et administration, distinct du pipeline n8n |
| `n8n/README.md` | Spécification nœud par nœud des workflows cible (WF-00…WF-11) ; `n8n/workflows/` reçoit les exports JSON |
| `n8n/build_pipeline.py`, `n8n/sync.py` | Construisent et déploient le workflow unique déjà en production sur l'instance n8n cloud partagée — voir plus bas, il diverge du découpage WF-xx du README |
| `.github/workflows/` | `ci.yml` (garde-fous + validation SQL/JSON) et `deploy-n8n.yml` (déploiement auto du pipeline n8n sur push) |
| `CREDENTIALS.md`, `docs/installation-vm.md` | Accès à réunir, et procédure pas à pas pour la VM (pour qui ne connaît pas Docker/SSH) |

**En cas de désaccord entre le code et le document, le document gagne** — ou bien on amende le document, explicitement. Une modification du schéma SQL implique une modification de la section 7 du HTML, et réciproquement.

Il n'y a ni build, ni lint. Il y a en revanche une vraie suite de tests, à faire tourner avant de prétendre qu'un changement fonctionne :

```bash
cd engine && python test_guardrails.py     # 24 tests des garde-fous, sans Docker ni clé API
```

La CI (`.github/workflows/ci.yml`) va plus loin qu'un développeur sur ce poste Windows ne peut le faire seul : elle lance un vrai conteneur PostgreSQL et y applique `001_schema.sql` → `004_reporting_views.sql`, et valide le JSON de `tools/agent-tools.json` et des `params_schema` des runbooks. Ni Docker ni PostgreSQL ne sont installés sur ce poste de développement — pour vérifier une modification SQL, la pousser et regarder tourner la CI plutôt que prétendre l'avoir validée localement.

## Conventions du socle

- **Aucun secret dans le dépôt, ni en base.** Les tables ne portent que des `credential_ref`, c'est-à-dire des *noms* de credentials n8n. Les jetons vivent dans le coffre n8n, la clé API et les mots de passe dans `.env` (ignoré par git).
- **Un adaptateur par plateforme, jamais par société.** Un nouveau groupe Telegram n'est pas un nouveau workflow, c'est une ligne dans `channels`.
- Les contrôles de sécurité s'écrivent **en code n8n après la réponse du modèle**, et sont documentés dans le tableau de `prompts/triage.md`. Les rappeler dans le prompt ne suffit jamais.
- Les workflows sont exportés en JSON dans `n8n/workflows/` et versionnés — c'est la seule façon de relire un changement de comportement.
- Le schéma évolue par nouveaux fichiers numérotés dans `sql/` (`003_*.sql`, …), jamais en modifiant `001_schema.sql` une fois la base en production.
- **`n8n/README.md` décrit la cible (WF-00 à WF-11, un sous-workflow par responsabilité) ; `n8n/build_pipeline.py` construit ce qui est réellement déployé** — un workflow unique `AgentSupport - Pipeline`, régénéré depuis `n8n/guardrails.js` et `prompts/triage.md`, poussé par CI (`deploy-n8n.yml`) sur l'instance n8n cloud **partagée**, toujours déployé inactif. `n8n/sync.py` refuse de toucher un workflow dont le nom ne commence pas par ce préfixe — ne jamais retirer ce garde-fou. Modifier le comportement du pipeline passe par `build_pipeline.py`, pas par l'éditeur n8n directement.

## Conventions d'édition du HTML

- **Langue : français.** Tout le contenu, y compris les commentaires SQL des blocs `<pre>`.
- **Aucune dépendance réseau.** Le `<style>` est inline dans le `<head>`, les schémas sont du SVG inline. Ne pas introduire de CDN, de webfont ni de script.
- **Ancres de section.** `<h2 id="s1">` … `id="s17">`, puis `id="sa"` / `id="sb"` pour les annexes. Le sommaire `<nav class="toc">` en tête du document renvoie vers ces ancres : **le mettre à jour à chaque ajout ou renumérotation de section**.
- **Blocs d'appel** : `<div class="note">` (encadré vert, point d'architecture) et `<div class="stop">` (encadré rouge, interdit ou avertissement), tous deux introduits par un `<span class="lbl">Titre</span>`.
- **Schémas** : `<figure>` contenant un `<svg class="diag">`, avec les classes de nœuds `n-gray` (canaux et sorties), `n-infra` (infrastructure et données), `n-ia` (raisonnement), les libellés `.th` / `.ts`, les flèches `.arr`, suivi d'une `<figcaption>`.
- Le bandeau `<dl class="meta">` porte la version du document. L'incrémenter à chaque lot d'amendements.

## L'architecture que le document spécifie

Cinq couches, avec un partage des rôles qui est le cœur de la conception :

```
Canaux (Telegram, Teams, Google Chat, mail, ClickUp, Jira)
   └─ n8n ......... écoute, normalise, déduplique, met en file, exécute, journalise
        └─ Claude . comprend, classe, choisit le niveau d'autonomie, rédige
             └─ PostgreSQL (référentiel unique + audit) · Qdrant (KB) · JSM (ticket visible)
```

**Claude ne se connecte à rien directement.** Toute action technique est un workflow n8n exposé comme outil. Une proposition qui donne à Claude un accès direct à un backend contredit la conception.

Trois axes structurent tout le reste :

- **Niveaux d'autonomie N0→N3** (§5) — répondre, exécuter après validation, guider, escalader. Décidés par le modèle, mais **replafonnés par du code n8n** après coup.
- **Paliers de maturité P0→P3** (§6) — le degré d'équipement d'une société (`max_autonomy`), qui plafonne durement le niveau d'autonomie. Une société démarre en P1 sans aucune intégration.
- **Multi-tenant** (§4) — un adaptateur par *plateforme*, jamais par société. Le canal d'origine détermine la société, qui détermine tout le contexte chargé à l'exécution.

## Invariants — les cinq règles (§2)

Ne jamais proposer d'amendement qui contredise ces règles ; si une décision technique entre en conflit, c'est la règle qui gagne.

1. Un seul référentiel de tickets — la table PostgreSQL `tickets`, source unique du reporting.
2. Rien n'est codé en dur — aucun nom de société, identifiant de groupe ou adresse mail dans le code ou le prompt. Tout vit en table de configuration.
3. Quatre niveaux d'autonomie — l'agent choisit dans un cadre défini, il ne décide pas de ses droits.
4. Toute action à impact passe par une validation humaine — au moins les six premiers mois.
5. Les règles de sécurité s'appliquent **en dehors du modèle** — les interdits, plafonds et contrôles de rôle sont revérifiés en code n8n après la réponse de Claude. Le prompt les rappelle, on ne compte jamais dessus.

Deux corollaires non négociables : la liste des **interdits permanents** (§10 — secrets et codes 2FA, comptes à privilèges, suppressions, données RH, production, prise en main à distance, identité non reconnue) et la table **`ticket_events`** (§7), qui est la seule réponse possible à « pourquoi l'agent a fait ça ? ».

## Contexte métier

Quatre entités au périmètre initial, avec des canaux hétérogènes : **BeautyBay** (ClickUp pour le dev, Jira/Confluence et `itsupport@` pour le support, Telegram), **Bazarchic** (Google Chat), **Bouchara** (Teams), **IT Support Liban** (Telegram, canal pilote). D'autres groupes Telegram suivront — d'où la règle 2.

Le rôle de n8n n'est pas de contourner la limite d'un connecteur par conversation sur claude.ai : par l'API et le Claude Agent SDK, le nombre de serveurs MCP et d'outils n'est pas limité. n8n est là pour ce que Claude ne fait pas — écouter en permanence, mettre en file, relancer, journaliser.

## Décisions arrêtées

Ces cinq points de la §17 sont tranchés et ne sont plus à rouvrir :

- **Référentiel** : Jira Service Management. ClickUp reste l'outil des devs BeautyBay, synchronisé en lecture vers la base unifiée.
- **Hébergement** : n8n auto-hébergé en Docker Compose (n8n + PostgreSQL + Qdrant + Caddy/Traefik pour le HTTPS, exigé par les webhooks Teams et Google Chat).
- **Canal pilote** : le groupe Telegram IT Support Liban — volume élevé, demandes répétitives, API Bot intégrable en une heure.
- **Équipe référente** : trois profils (responsable IT arbitre, technicien N1 rédacteur de runbooks, profil dev/ops sur n8n), 2 à 4 heures par semaine chacun.
- **Interdits permanents** : liste figée, §10.

Restent ouverts : politique 2FA (recommandation du document : gestionnaire de secrets d'équipe avec TOTP), taxonomie initiale à extraire de l'historique du canal pilote, rétention et information RGPD, traitement des pièces jointes.

**Écart constaté entre décision et réalité de terrain** : le document tranche pour un n8n auto-hébergé en Docker Compose, mais `deploy-n8n.yml` déploie vers une instance **n8n cloud partagée** (variable `N8N_BASE_URL`, type `*.app.n8n.cloud`). Avant de faire une hypothèse sur l'hébergement réel, vérifier laquelle des deux est effectivement en service plutôt que de se fier à l'un ou l'autre.
