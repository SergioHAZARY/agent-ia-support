-- =============================================================================
-- Migration 005 : ajout des sociétés Bazarchic et François Saget
--
-- Bazarchic  → tickets Jira (bzcmtc.atlassian.net)
-- François Saget → demandes par email Outlook (Support-IT@francoisesaget.com)
--
-- Les deux démarrent en P1 (triage seul, max_autonomy = 'N3').
-- =============================================================================

-- ----------------------------- Tenants --------------------------------------

insert into tenants (code, name, timezone, languages, max_autonomy)
values ('bazarchic', 'Bazarchic', 'Europe/Paris', '{fr}', 'N3')
on conflict (code) do nothing;

insert into tenants (code, name, timezone, languages, max_autonomy)
values ('fsaget', 'François Saget', 'Europe/Paris', '{fr}', 'N3')
on conflict (code) do nothing;

-- ----------------------------- Politiques -----------------------------------

insert into tenant_policies (tenant_id, business_hours, offhours_message,
  ticket_backend, kb_source, identity_source, trigger_mode)
select id,
  '{"mon":["09:00","18:00"],"tue":["09:00","18:00"],"wed":["09:00","18:00"],"thu":["09:00","18:00"],"fri":["09:00","18:00"]}'::jsonb,
  'Votre demande a bien été reçue. Elle sera traitée aux heures ouvrables.',
  'none', 'none', 'none', 'auto'
from tenants where code = 'bazarchic'
on conflict (tenant_id) do nothing;

insert into tenant_policies (tenant_id, business_hours, offhours_message,
  ticket_backend, kb_source, identity_source, trigger_mode)
select id,
  '{"mon":["08:30","17:30"],"tue":["08:30","17:30"],"wed":["08:30","17:30"],"thu":["08:30","17:30"],"fri":["08:30","17:30"]}'::jsonb,
  'Votre demande a bien été reçue. Elle sera traitée aux heures ouvrables.',
  'none', 'none', 'none', 'auto'
from tenants where code = 'fsaget'
on conflict (tenant_id) do nothing;

-- ----------------------------- Canaux ---------------------------------------

-- Jira Bazarchic : tous les événements du site bzcmtc.atlassian.net
insert into channels (tenant_id, platform, external_id, display_name, status)
select id, 'jira', 'bzcmtc.atlassian.net', 'Bazarchic — Jira', 'active'
from tenants where code = 'bazarchic'
on conflict (platform, external_id) do nothing;

-- Email François Saget : boîte Outlook Support-IT@francoisesaget.com
insert into channels (tenant_id, platform, external_id, display_name, status)
select id, 'email', 'support-it@francoisesaget.com', 'François Saget — Outlook', 'active'
from tenants where code = 'fsaget'
on conflict (platform, external_id) do nothing;

-- ----------------------------- Vérification ----------------------------------
-- select code, name from tenants order by code;
-- select platform, external_id, display_name, status from channels order by platform;
