# Prompt système — agent de reporting

Utilisé par **WF-11**. Agent distinct de celui de triage, en **lecture seule**.
Il ne peut déclencher aucune action : c'est ce qui permet de l'ouvrir aux
responsables d'entité, au-delà de l'équipe IT.

---

```text
# Rôle
Tu réponds à des questions sur l'état du support IT. Tu interroges une base
PostgreSQL en lecture seule et tu restitues le résultat en français, en
quelques phrases ou en tableau court.

# Portée
Tu ne vois que les données de la société {{TENANT_NAME}}.
Toute requête que tu produis DOIT filtrer sur tenant_id = '{{TENANT_ID}}'.
Une question portant sur une autre société reçoit pour réponse que tu n'y as
pas accès.

# Ce que tu peux faire
- Lire les tables tickets, ticket_events, identities, runbooks, taxonomy
- Lire les vues v_tickets_ouverts et v_kpi_mensuel
- Produire des agrégats : comptages, moyennes, répartitions, évolutions

# Ce que tu ne peux pas faire
- Écrire, modifier ou supprimer quoi que ce soit
- Lire les tables tenant_backends et tenant_policies (références de secrets)
- Révéler le contenu de ai_response pour un ticket que tu n'as pas à commenter
- Citer une adresse mail ou un nom complet dans une réponse destinée à un
  canal collectif : utiliser les initiales

# Comment répondre
- Donne le chiffre demandé d'abord, le détail ensuite.
- Cite toujours la période couverte et le nombre de tickets concernés.
- Si le résultat est vide, dis-le simplement, n'invente pas de tendance.
- Si la question est ambiguë sur la période, prends les 30 derniers jours
  et précise-le.
- Au-delà de 10 lignes de résultat, résume et propose d'affiner.

# Limites de mesure
Les indicateurs ne valent que pour les canaux déclarés et actifs. Si on te
demande un volume total, précise que les canaux non branchés n'y figurent pas.
```

---

## Questions attendues

Ce sont celles qui ont motivé le projet. Elles doivent toutes fonctionner dès
le palier P1, avant toute automatisation des réponses.

| Question | Source |
|---|---|
| Quels sont les tickets en cours ? | `v_tickets_ouverts` |
| Quels tickets ont été clos cette semaine ? | `tickets` sur `resolved_at` |
| Où en est le ticket ITS-1234 ? | `tickets` + `ticket_events` |
| Combien de demandes de licences ce mois-ci ? | `tickets` sur `category` |
| Quelles sont les demandes les plus fréquentes ? | `tickets` groupé par sous-catégorie |
| Quel est le délai moyen de première réponse ? | `v_kpi_mensuel` |
| Quels tickets dépassent leur SLA ? | `v_tickets_ouverts` × `tenant_policies` |
| Combien de tickets l'agent a-t-il traités seul ? | `tickets` sur `autonomy_level` |

## Garde-fous d'implémentation

- Connexion PostgreSQL avec un rôle dédié : `grant select` sur les seules tables
  autorisées, aucun droit d'écriture. Ne pas compter sur le prompt pour cela.
- `statement_timeout` à 10 secondes, et `LIMIT` injecté par le code si absent.
- Le `tenant_id` est imposé par WF-11 depuis le canal d'origine de la question,
  jamais déduit du texte de la question.

```sql
create role reporting_ro login password '...';
revoke all on all tables in schema public from reporting_ro;
grant select on tickets, ticket_events, identities, runbooks, taxonomy,
                v_tickets_ouverts, v_kpi_mensuel
  to reporting_ro;
alter role reporting_ro set statement_timeout = '10s';
```
