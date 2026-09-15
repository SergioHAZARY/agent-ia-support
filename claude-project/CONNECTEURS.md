# Connecter le projet Claude — guide

Deux couches à ne jamais confondre :

- **Automatisation 24/7 des canaux → n8n.** C'est n8n qui écoute Telegram,
  Teams, Google Chat, mail, ClickUp, Jira en permanence. Les jetons de ces
  canaux vivent dans les *credentials n8n*, pas dans claude.ai.
- **Poste de pilotage humain → projet claude.ai.** Les connecteurs ci-dessous
  te servent à TOI pour interroger et agir ponctuellement.

## Connecteurs à activer dans claude.ai

claude.ai → ta photo → **Réglages → Connecteurs** (ou le panneau droit du
projet). Pour chacun : **Autoriser**, puis connecter le compte. Toi seul peux
faire cette étape (OAuth).

| Connecteur | Sert à | Remarque |
|---|---|---|
| **Supabase** | Lire les tickets (reporting) | le plus important ici |
| **n8n** | Voir / déclencher les workflows | pilotage |
| **Atlassian Rovo** | Jira + Confluence | tickets dev, base de connaissances |
| **ClickUp** | Tickets dev BeautyBay | |
| **Microsoft 365** | Teams + mail Outlook | |
| **Notion** | Documentation éventuelle | facultatif |
| Gmail / Agenda / Drive | déjà disponibles | Google Workspace |

## Pas de connecteur first-party

- **Telegram** et **Google Chat** n'ont pas de connecteur claude.ai. Ils
  passent exclusivement par n8n (déjà en place dans le pipeline).

## Ajouter le Skill

Le dossier `skills/support-agent/` contient un Skill (`SKILL.md`) qui apprend à
Claude à piloter l'agent (reporting, niveaux, interdits, ajout de société).

Pour l'installer sur claude.ai : Réglages → **Capacités / Skills** → ajouter un
skill → téléverser le dossier `support-agent` (ou son `SKILL.md`). Une fois
ajouté, il s'applique à tes conversations du projet.

## Ordre conseillé

1. Coller `claude-project/INSTRUCTIONS.md` dans les Instructions du projet.
2. Ajouter le Skill `support-agent`.
3. Autoriser le connecteur **Supabase** (reporting immédiat).
4. Autoriser les autres connecteurs au fur et à mesure des besoins.
