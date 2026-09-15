-- =============================================================================
-- Catalogue de runbooks — les « compétences » de l'agent
--
-- Trois principes, à comprendre avant d'ajouter une ligne :
--
-- 1. Les runbooks N2 (guider) ne demandent aucun backend : ils marchent partout,
--    dès le premier jour, même pour une société sans aucune intégration.
--
-- 2. Les runbooks N1 (exécuter) restent GLOBAUX mais portent un `backend`.
--    WF-02 ne les propose qu'aux sociétés ayant déclaré ce backend comme actif
--    dans tenant_backends (voir la vue v_runbooks_disponibles en fin de fichier).
--    C'est ce qui évite de dupliquer le catalogue pour chaque société.
--
-- 3. Une société peut surcharger n'importe quel runbook global : insérer une
--    ligne de même `code` avec son `tenant_id`. Le spécifique gagne.
--
-- Rien ici ne contourne les interdits permanents (§10). Aucun runbook ne
-- transmet de secret, ne crée de compte à privilèges, ne supprime de données
-- ni ne prend la main à distance. Ces demandes sont N3, sans exception.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- N1 — COMPTES ET IDENTITÉS
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, backend, execution_mode,
   approval_required, manager_approval, allowed_roles, preconditions, params_schema)
values

  (null, 'AD_CREATE_USER',
   'Créer un compte dans l''annuaire (nouvel arrivant)',
   'compte_identite', 'N1', 'active_directory', 'approval', true, true,
   '{manager,it,admin}',
   'Le demandeur est manager, IT ou admin. La date d''arrivée est passée ou dans les 7 jours. '
   'Le compte est créé dans l''unité d''organisation du service, jamais à la racine. '
   'Aucun groupe à privilèges n''est ajouté à la création.',
   '{"type":"object","required":["prenom","nom","service","manager_email","date_arrivee"],
     "additionalProperties": false,
     "properties":{
       "prenom":{"type":"string","minLength":1},
       "nom":{"type":"string","minLength":1},
       "service":{"type":"string"},
       "manager_email":{"type":"string","format":"email"},
       "date_arrivee":{"type":"string","format":"date"},
       "intitule_poste":{"type":"string"}
     }}'::jsonb),

  (null, 'AD_RESET_PASSWORD',
   'Réinitialiser le mot de passe d''un utilisateur',
   'compte_identite', 'N1', 'active_directory', 'approval', true, false,
   '{it,admin}',
   'VÉRIFICATION D''IDENTITÉ HORS CANAL OBLIGATOIRE : appel vidéo, validation du '
   'manager, ou question de vérification. Le scénario « j''ai changé de téléphone, '
   'réinitialisez-moi » est le vecteur d''ingénierie sociale le plus courant. '
   'Le mot de passe temporaire n''est JAMAIS envoyé dans le canal : il est remis '
   'par un autre moyen, et le changement à la première connexion est forcé.',
   '{"type":"object","required":["user_email","verification_methode"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "verification_methode":{"type":"string",
         "enum":["appel_video","validation_manager","question_verification"]},
       "verifie_par":{"type":"string"}
     }}'::jsonb),

  (null, 'AD_DISABLE_USER',
   'Désactiver un compte (départ de collaborateur)',
   'compte_identite', 'N1', 'active_directory', 'approval', true, true,
   '{manager,it,admin}',
   'Désactivation uniquement — jamais de suppression, qui est un interdit permanent. '
   'Les sessions sont révoquées, la boîte est convertie en boîte partagée si le '
   'manager le demande. La suppression éventuelle est une décision humaine, plus tard.',
   '{"type":"object","required":["user_email","date_depart"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "date_depart":{"type":"string","format":"date"},
       "transfert_vers":{"type":"string","format":"email"},
       "conserver_boite_jours":{"type":"integer","minimum":0,"maximum":365}
     }}'::jsonb),

  (null, 'AD_ADD_TO_GROUP',
   'Ajouter un utilisateur à un groupe de sécurité',
   'compte_identite', 'N1', 'active_directory', 'approval', true, true,
   '{manager,it,admin}',
   'Le groupe demandé ne doit pas être un groupe à privilèges (Domain Admins, '
   'Enterprise Admins, Schema Admins, ou tout groupe listé comme sensible). '
   'Toute demande portant sur un de ces groupes est rabaissée en N3 par le code.',
   '{"type":"object","required":["user_email","groupe"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "groupe":{"type":"string"},
       "motif":{"type":"string"}
     }}'::jsonb),

  (null, 'ENTRA_CREATE_USER',
   'Créer un compte dans Entra ID',
   'compte_identite', 'N1', 'entra', 'approval', true, true,
   '{manager,it,admin}',
   'À n''utiliser QUE si l''annuaire sur site n''est plus la source de vérité. '
   'En environnement hybride avec synchronisation, écrire dans le cloud est écrasé '
   'à la synchronisation suivante : il faut alors AD_CREATE_USER.',
   '{"type":"object","required":["prenom","nom","service","manager_email"],
     "additionalProperties": false,
     "properties":{
       "prenom":{"type":"string"},
       "nom":{"type":"string"},
       "service":{"type":"string"},
       "manager_email":{"type":"string","format":"email"},
       "usage_location":{"type":"string","minLength":2,"maxLength":2}
     }}'::jsonb),

  (null, 'GW_CREATE_USER',
   'Créer un compte Google Workspace',
   'compte_identite', 'N1', 'google_workspace', 'approval', true, true,
   '{manager,it,admin}',
   'Le compte est créé dans l''unité organisationnelle du service. '
   'Aucun rôle d''administration n''est attribué.',
   '{"type":"object","required":["prenom","nom","service","manager_email"],
     "additionalProperties": false,
     "properties":{
       "prenom":{"type":"string"},
       "nom":{"type":"string"},
       "service":{"type":"string"},
       "manager_email":{"type":"string","format":"email"},
       "unite_org":{"type":"string"}
     }}'::jsonb)

on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N1 — MESSAGERIE
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, backend, execution_mode,
   approval_required, manager_approval, allowed_roles, preconditions, params_schema)
values

  (null, 'MAIL_CREATE_MAILBOX',
   'Créer une boîte aux lettres',
   'messagerie', 'N1', 'm365', 'approval', true, true,
   '{manager,it,admin}',
   'Le compte utilisateur existe déjà. Respecter la convention de nommage de la '
   'société, documentée dans sa base de connaissances — elle diffère d''une entité '
   'à l''autre.',
   '{"type":"object","required":["user_email"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "type":{"type":"string","enum":["utilisateur","partagee"],"default":"utilisateur"},
       "quota_go":{"type":"integer","minimum":1,"maximum":100}
     }}'::jsonb),

  (null, 'MAIL_ADD_ALIAS',
   'Ajouter un alias à une boîte existante',
   'messagerie', 'N1', 'm365', 'approval', true, false,
   '{employe,manager,it,admin}',
   'Un employé ne peut demander un alias que pour sa propre boîte. Pour la boîte '
   'd''un tiers, le rôle manager, it ou admin est requis.',
   '{"type":"object","required":["boite","alias"],
     "additionalProperties": false,
     "properties":{
       "boite":{"type":"string","format":"email"},
       "alias":{"type":"string","format":"email"},
       "principal":{"type":"boolean","default":false}
     }}'::jsonb),

  (null, 'MAIL_DISTRIBUTION_MEMBER',
   'Ajouter ou retirer un membre d''une liste de diffusion',
   'messagerie', 'N1', 'm365', 'approval', true, false,
   '{manager,it,admin}',
   'Les listes marquées comme sensibles (direction, paie, sécurité) sont exclues : '
   'toute demande les concernant est N3.',
   '{"type":"object","required":["liste","user_email","operation"],
     "additionalProperties": false,
     "properties":{
       "liste":{"type":"string","format":"email"},
       "user_email":{"type":"string","format":"email"},
       "operation":{"type":"string","enum":["ajouter","retirer"]}
     }}'::jsonb),

  (null, 'MAIL_INCREASE_QUOTA',
   'Augmenter le quota d''une boîte',
   'messagerie', 'N1', 'm365', 'approval', true, false,
   '{it,admin}',
   'Proposer d''abord GUIDE_MAILBOX_QUOTA : l''archivage règle le problème sans coût. '
   'L''augmentation de quota peut engager une dépense — au-delà du seuil défini, N3.',
   '{"type":"object","required":["boite","nouveau_quota_go"],
     "additionalProperties": false,
     "properties":{
       "boite":{"type":"string","format":"email"},
       "nouveau_quota_go":{"type":"integer","minimum":1,"maximum":100}
     }}'::jsonb)

on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N1 — LICENCES ET ACCÈS APPLICATIFS
-- C'est la catégorie la plus demandée dans les groupes de support.
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, backend, execution_mode,
   approval_required, manager_approval, allowed_roles, preconditions, params_schema)
values

  (null, 'LICENCE_ASSIGN',
   'Attribuer une licence logicielle à un utilisateur',
   'acces_licences', 'N1', 'm365', 'approval', true, false,
   '{employe,manager,it,admin}',
   'Un employé ne peut demander une licence que pour lui-même. '
   'Vérifier qu''un poste est disponible avant d''en acheter un : si le parc est '
   'saturé, la demande engage une dépense et passe en N3.',
   '{"type":"object","required":["user_email","produit"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "produit":{"type":"string"},
       "formule":{"type":"string"}
     }}'::jsonb),

  (null, 'LICENCE_REVOKE',
   'Libérer une licence',
   'acces_licences', 'N1', 'm365', 'approval', true, false,
   '{manager,it,admin}',
   'Prévenir le titulaire avant révocation, sauf dans le cadre d''un départ déjà '
   'traité par AD_DISABLE_USER.',
   '{"type":"object","required":["user_email","produit"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "produit":{"type":"string"},
       "motif":{"type":"string"}
     }}'::jsonb),

  (null, 'SAAS_INVITE_SEAT',
   'Inviter un utilisateur sur un outil SaaS de l''entreprise',
   'acces_licences', 'N1', 'saas_admin', 'approval', true, false,
   '{employe,manager,it,admin}',
   'Couvre les demandes du type « ajoute-moi à tel outil ». L''outil doit figurer '
   'dans le catalogue de la société. Un employé ne demande que pour lui-même. '
   'L''invitation part sur l''adresse professionnelle, jamais personnelle.',
   '{"type":"object","required":["user_email","outil"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "outil":{"type":"string"},
       "role":{"type":"string","default":"membre"},
       "espace":{"type":"string"}
     }}'::jsonb),

  (null, 'VAULT_GRANT_ACCESS',
   'Donner accès à un secret partagé dans le coffre d''équipe',
   'acces_licences', 'N1', 'vault', 'approval', true, true,
   '{manager,it,admin}',
   'C''est la réponse correcte aux demandes de code 2FA sur compte partagé : on ne '
   'transmet pas le code, on donne l''accès au coffre et la personne lit le code '
   'elle-même. L''agent ne lit JAMAIS le contenu du secret, il n''en indique que '
   'l''emplacement, et uniquement en message privé.',
   '{"type":"object","required":["user_email","collection"],
     "additionalProperties": false,
     "properties":{
       "user_email":{"type":"string","format":"email"},
       "collection":{"type":"string"},
       "droit":{"type":"string","enum":["lecture","lecture_ecriture"],"default":"lecture"},
       "duree_jours":{"type":"integer","minimum":1,"maximum":365}
     }}'::jsonb)

on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N2 — GUIDER. Aucun backend, donc disponible partout dès le palier P2.
-- Ce sont ces runbooks qui font le volume les premières semaines.
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, execution_mode,
   approval_required, allowed_roles, preconditions)
values
  (null, 'GUIDE_MFA_NOUVEAU_TELEPHONE',
   'Reconfigurer son MFA après un changement de téléphone',
   'compte_identite', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Uniquement si le demandeur a encore une méthode de vérification valide '
   '(ancien appareil, code de secours). Sinon c''est une réinitialisation MFA : '
   'N3, vérification d''identité hors canal obligatoire.'),

  (null, 'GUIDE_OUTLOOK_RECONNEXION',
   'Reconnecter Outlook qui redemande le mot de passe en boucle',
   'messagerie', 'N2', 'manual', false, '{employe,manager,it,admin}', null),

  (null, 'GUIDE_TEAMS_CACHE',
   'Vider le cache Teams quand l''application ne démarre plus',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}', null),

  (null, 'GUIDE_WIFI_INVITE',
   'Connecter un visiteur au réseau Wi-Fi invité',
   'reseau', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Le réseau invité est isolé du réseau interne. Ne jamais communiquer la clé '
   'du réseau interne à un visiteur.'),

  (null, 'GUIDE_STOCKAGE_PARTAGE',
   'Retrouver ou remonter un lecteur réseau',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}', null),

  (null, 'GUIDE_NOUVEL_ARRIVANT',
   'Checklist du premier jour pour un nouvel arrivant',
   'information', 'N2', 'manual', false, '{manager,it,admin}',
   'Renvoie vers la procédure de la société. Les actions techniques associées '
   'sont des runbooks N1 distincts, chacun avec sa validation.'),

  (null, 'GUIDE_DEMANDE_MATERIEL',
   'Demander du matériel (écran, casque, station d''accueil)',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'L''agent collecte le besoin et le justificatif, puis escalade : une commande '
   'engage une dépense, elle n''est jamais automatisée.'),

  (null, 'GUIDE_TICKET_DEV_COMPLET',
   'Compléter une demande de développement',
   'developpement', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'L''agent ne code pas. Il réclame ce qui manque — environnement, étapes de '
   'reproduction, capture, comportement attendu — détecte les doublons, propose '
   'une priorité, puis route vers l''équipe de développement.')

on conflict do nothing;

-- =============================================================================
-- La vue que WF-02 interroge.
--
-- Un runbook n'est proposé à l'agent que si :
--   - il est actif,
--   - il ne demande aucun backend (N0/N2), OU la société a déclaré ce backend,
--   - son niveau ne dépasse pas le plafond de maturité de la société,
--   - et, à code égal, la version spécifique écrase la globale.
-- =============================================================================
create or replace view v_runbooks_disponibles as
with classe as (
  -- On expose l'id de la SOCIÉTÉ comme tenant_id (le runbook peut être global).
  -- Colonnes listées explicitement : un r.* réintroduirait runbooks.tenant_id
  -- et rendrait la référence ambiguë.
  select t.id as tenant_id, t.max_autonomy,
         r.code, r.title, r.category, r.autonomy_level, r.backend,
         r.execution_mode, r.approval_required, r.manager_approval,
         r.allowed_roles, r.preconditions, r.params_schema, r.n8n_webhook,
         -- 1 = runbook propre à la société, 2 = global. Le plus petit gagne.
         case when r.tenant_id is null then 2 else 1 end as rang
  from tenants t
  join runbooks r
    on r.tenant_id = t.id or r.tenant_id is null
  where t.active
    and r.active
    -- le backend requis doit être déclaré et actif chez cette société
    and (r.backend is null or exists (
          select 1 from tenant_backends b
          where b.tenant_id = t.id and b.kind = r.backend and b.active))
    -- palier P0 : l'agent observe, on ne lui propose aucune action
    and not t.observation_only
    -- le plafond de maturité de la société s'applique
    and case t.max_autonomy
          when 'N1' then r.autonomy_level in ('N0','N1','N2')  -- P3 : exécute
          when 'N2' then r.autonomy_level in ('N0','N2')       -- P2 : répond et guide
          else false                                            -- N3 / P1 : triage seul
        end
)
select distinct on (tenant_id, code)
       tenant_id, code, title, category, autonomy_level, backend,
       execution_mode, approval_required, manager_approval, allowed_roles,
       preconditions, params_schema, n8n_webhook
from classe
order by tenant_id, code, rang;

comment on view v_runbooks_disponibles is
  'Runbooks réellement proposables à l''agent, par société. WF-02 lit ici, jamais la table brute.';
