-- =============================================================================
-- Agent IA de support multicanal — schéma de référence
-- Correspond à la section 7 du document d'implémentation (v1.1).
--
-- Principe : rien n'est codé en dur. Aucun nom de société, aucun identifiant
-- de groupe, aucune adresse mail ne vit ailleurs que dans ces tables.
--
-- Appliquer avec :  psql "$DATABASE_URL" -f sql/001_schema.sql
-- =============================================================================

create extension if not exists "pgcrypto";   -- gen_random_uuid()

-- =============================================================================
-- 1. CONFIGURATION
-- =============================================================================

-- Une société / entité cliente. Ajouter une entité = insérer une ligne ici.
create table if not exists tenants (
  id            uuid primary key default gen_random_uuid(),
  code          text unique not null,        -- identifiant court, ex. 'acme'
  name          text not null,
  timezone      text not null default 'Europe/Paris',
  languages     text[] not null default '{fr,en}',
  -- Palier de maturité : plafond dur appliqué APRÈS la réponse du modèle.
  -- N3 = palier P1 (triage seul) · N2 = P2 (répond) · N1 = P3 (exécute).
  max_autonomy  text not null default 'N3',
  -- Palier P0 : l'agent lit, classe et enregistre, mais ne publie rien.
  -- Sert à mesurer le volume et à calibrer la taxonomie sans déranger personne.
  observation_only boolean not null default false,
  active        boolean not null default true,
  created_at    timestamptz not null default now(),
  constraint tenants_max_autonomy_chk
    check (max_autonomy in ('N1','N2','N3'))
);

comment on column tenants.max_autonomy is
  'Plafond d''autonomie. Même si le modèle propose N1, une société en N3 escalade. '
  'Appliqué par le code n8n après la réponse du modèle, jamais par le prompt.';
comment on column tenants.observation_only is
  'Palier P0. L''agent traite le message et crée le ticket, mais WF-06 ne publie rien.';

-- Politiques de fonctionnement, une ligne par société.
create table if not exists tenant_policies (
  tenant_id           uuid primary key references tenants(id) on delete cascade,
  business_hours      jsonb,   -- {"mon":["09:00","18:00"], ..., "sat":null}
  offhours_message    text,
  escalation_channel  text,    -- où poster les N3
  approval_channel    text,    -- où poster les cartes de validation
  admin_channel       text,    -- nouveaux canaux, alertes techniques
  sla_p1_minutes      int  not null default 60,
  sla_p2_minutes      int  not null default 240,
  sla_p3_minutes      int  not null default 1440,
  sla_p4_minutes      int  not null default 4320,
  ticket_backend      text not null default 'none',      -- jsm | clickup | none
  ticket_project_key  text,
  kb_source           text not null default 'none',      -- confluence | notion | none
  kb_space_key        text,
  identity_source     text not null default 'none',      -- active_directory | entra | google | manual | none
  trigger_mode        text not null default 'mention',   -- mention | auto
  data_retention_days int  not null default 365,
  constraint tenant_policies_ticket_backend_chk
    check (ticket_backend in ('jsm','clickup','none')),
  constraint tenant_policies_kb_source_chk
    check (kb_source in ('confluence','notion','none')),
  constraint tenant_policies_identity_source_chk
    check (identity_source in ('active_directory','entra','google','manual','none')),
  constraint tenant_policies_trigger_mode_chk
    check (trigger_mode in ('mention','auto'))
);

-- Les canaux : c'est cette table qui rend le système dynamique.
-- Un canal inconnu est inséré en 'pending' par WF-00 et reste muet.
create table if not exists channels (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid references tenants(id) on delete cascade,
  platform         text not null,   -- telegram | teams | gchat | email | clickup | jira
  external_id      text not null,   -- id du groupe, du canal, ou adresse mail
  display_name     text,
  purpose          text not null default 'support',  -- support | dev | alerting
  default_priority text not null default 'p3',
  status           text not null default 'pending',  -- pending | active | disabled
  credential_ref   text,            -- nom d'une credential n8n, jamais un secret
  created_at       timestamptz not null default now(),
  unique (platform, external_id),
  constraint channels_platform_chk
    check (platform in ('telegram','teams','gchat','email','clickup','jira','confluence')),
  constraint channels_status_chk
    check (status in ('pending','active','disabled')),
  constraint channels_purpose_chk
    check (purpose in ('support','dev','alerting')),
  -- un canal actif doit être rattaché à une société
  constraint channels_active_needs_tenant_chk
    check (status <> 'active' or tenant_id is not null)
);

create index if not exists channels_tenant_idx on channels (tenant_id);
create index if not exists channels_lookup_idx on channels (platform, external_id);

-- Les backends d'exécution, déclarés par société.
-- Jamais de secret ici : seulement la référence à une credential n8n.
create table if not exists tenant_backends (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references tenants(id) on delete cascade,
  kind           text not null,   -- active_directory | entra | google_workspace | m365 | vault
  base_url       text,
  credential_ref text not null,
  active         boolean not null default true,
  unique (tenant_id, kind)
);

-- =============================================================================
-- 2. IDENTITÉS ET ACTIONS
-- =============================================================================

-- Qui a le droit de demander quoi.
-- Un expéditeur inconnu est créé avec verified = false : il peut poser des
-- questions (N0/N2) mais ne déclenche aucune action.
create table if not exists identities (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid references tenants(id) on delete cascade,
  full_name        text not null,
  email            text,
  role             text not null default 'employe',  -- employe | manager | it | admin
  telegram_user_id text,
  teams_user_id    text,
  google_user_id   text,
  verified         boolean not null default false,
  active           boolean not null default true,
  created_at       timestamptz not null default now(),
  unique (tenant_id, email),
  constraint identities_role_chk
    check (role in ('employe','manager','it','admin'))
);

-- Un même identifiant de plateforme ne peut désigner deux personnes.
create unique index if not exists identities_telegram_uidx
  on identities (telegram_user_id) where telegram_user_id is not null;
create unique index if not exists identities_teams_uidx
  on identities (teams_user_id) where teams_user_id is not null;
create unique index if not exists identities_google_uidx
  on identities (google_user_id) where google_user_id is not null;

-- Les "skills" : les procédures que l'agent peut proposer ou exécuter.
-- tenant_id = null → runbook global, valable partout.
-- Un runbook de même code avec un tenant_id renseigné écrase le global.
create table if not exists runbooks (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid references tenants(id) on delete cascade,
  code              text not null,          -- ex. AD_CREATE_USER
  title             text not null,
  category          text,
  autonomy_level    text,                   -- N0 | N1 | N2
  backend           text,                   -- active_directory | google_workspace | ...
  n8n_webhook       text,
  params_schema     jsonb,                  -- JSON Schema des paramètres attendus
  preconditions     text,
  execution_mode    text not null default 'approval',  -- auto | approval | manual
  approval_required boolean not null default true,
  manager_approval  boolean not null default false,
  allowed_roles     text[] not null default '{employe}',
  active            boolean not null default true,
  created_at        timestamptz not null default now(),
  constraint runbooks_autonomy_chk
    check (autonomy_level in ('N0','N1','N2')),
  constraint runbooks_execution_mode_chk
    check (execution_mode in ('auto','approval','manual'))
);

-- Unicité du code : une fois pour les globaux, une fois par société.
create unique index if not exists runbooks_global_code_uidx
  on runbooks (code) where tenant_id is null;
create unique index if not exists runbooks_tenant_code_uidx
  on runbooks (tenant_id, code) where tenant_id is not null;

-- =============================================================================
-- 3. ÉVÉNEMENTS ET TICKETS
-- =============================================================================

-- Tout message entrant, enregistré AVANT toute interprétation.
-- C'est le filet de sécurité : si le triage tombe, rien n'est perdu, on rejoue.
create table if not exists events (
  id                bigserial primary key,
  channel_id        uuid references channels(id) on delete set null,
  tenant_id         uuid references tenants(id) on delete set null,
  source_message_id text not null,
  sender_raw        text,
  identity_id       uuid references identities(id) on delete set null,
  body              text not null,
  attachments       jsonb,
  lang              text,
  ticket_id         uuid,
  processed_at      timestamptz,   -- null = pas encore trié
  received_at       timestamptz not null default now(),
  unique (channel_id, source_message_id)   -- déduplication
);

create index if not exists events_unprocessed_idx
  on events (received_at) where processed_at is null;

-- Le ticket normalisé, toutes sources confondues.
-- C'est CETTE table qui sert au reporting, jamais Jira ni ClickUp.
create table if not exists tickets (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid references tenants(id) on delete set null,
  channel_id         uuid references channels(id) on delete set null,
  ref                text,        -- clé Jira ou ClickUp, ex. ITS-1234
  conversation_id    text,        -- regroupe les messages d'un même échange
  requester_id       uuid references identities(id) on delete set null,
  requester_raw      text,
  title              text not null,
  summary            text,
  category           text,
  subcategory        text,
  priority           text,        -- p1 | p2 | p3 | p4
  autonomy_level     text,        -- N0 | N1 | N2 | N3
  confidence         numeric(3,2),
  status             text not null default 'nouveau',
  assignee           text,
  ai_response        text,        -- ce que l'agent a proposé
  final_response     text,        -- ce qui a réellement été envoyé
  validated_by       text,
  escalation_reason  text,
  created_at         timestamptz not null default now(),
  first_response_at  timestamptz,
  resolved_at        timestamptz,
  resolution_note    text,
  time_spent_minutes int,         -- saisi à la clôture
  satisfaction       smallint,    -- 1 à 5, demandé après clôture
  constraint tickets_priority_chk
    check (priority in ('p1','p2','p3','p4')),
  constraint tickets_autonomy_chk
    check (autonomy_level in ('N0','N1','N2','N3')),
  constraint tickets_status_chk
    check (status in ('nouveau','en_attente_validation','en_cours','en_attente_demandeur','resolu','clos','rejete')),
  constraint tickets_satisfaction_chk
    check (satisfaction between 1 and 5)
);

create unique index if not exists tickets_ref_uidx on tickets (ref) where ref is not null;
create index if not exists tickets_tenant_status_idx on tickets (tenant_id, status);
create index if not exists tickets_created_idx on tickets (created_at desc);
create index if not exists tickets_conversation_idx on tickets (conversation_id)
  where conversation_id is not null;

-- Journal d'audit. Non optionnel : c'est la seule réponse possible à
-- « pourquoi l'agent a fait ça ? ».
create table if not exists ticket_events (
  id         bigserial primary key,
  ticket_id  uuid not null references tickets(id) on delete cascade,
  actor      text not null,   -- 'agent' ou le nom du technicien
  action     text not null,   -- triage | proposition | approbation | rejet
                              -- | execution | reponse | cloture
  payload    jsonb,
  created_at timestamptz not null default now(),
  constraint ticket_events_action_chk
    check (action in ('triage','proposition','approbation','rejet',
                      'execution','echec','reponse','cloture','reouverture'))
);

create index if not exists ticket_events_ticket_idx on ticket_events (ticket_id, created_at);

-- =============================================================================
-- 4. VUES DE REPORTING
-- Utilisées par WF-11 (agent de reporting, lecture seule).
-- =============================================================================

create or replace view v_tickets_ouverts as
select t.id, t.ref, tn.code as societe, t.title, t.category, t.priority,
       t.autonomy_level, t.status, t.assignee, t.created_at,
       extract(epoch from (now() - t.created_at)) / 3600 as age_heures
from tickets t
left join tenants tn on tn.id = t.tenant_id
where t.status not in ('resolu','clos','rejete')
order by t.priority, t.created_at;

create or replace view v_kpi_mensuel as
select tn.code as societe,
       date_trunc('month', t.created_at) as mois,
       count(*)                                              as tickets,
       count(*) filter (where t.autonomy_level in ('N0','N1'))
         / nullif(count(*), 0)::numeric                      as taux_deviation,
       count(*) filter (where t.ai_response is distinct from t.final_response)
         / nullif(count(*) filter (where t.ai_response is not null), 0)::numeric
                                                             as taux_modification,
       avg(extract(epoch from (t.first_response_at - t.created_at)) / 60)
                                                             as minutes_premiere_reponse,
       avg(t.time_spent_minutes)                             as minutes_technicien,
       avg(t.satisfaction)                                   as satisfaction
from tickets t
left join tenants tn on tn.id = t.tenant_id
group by 1, 2
order by 2 desc, 1;
