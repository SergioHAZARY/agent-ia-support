-- =============================================================================
-- Migration 006 : ajout de la société Beauty Bay
--
-- Beauty Bay → tickets Jira (beautybay.atlassian.net)
-- Démarre en P1 (triage seul, max_autonomy = 'N3').
-- =============================================================================

-- ----------------------------- Tenant ----------------------------------------

insert into tenants (code, name, timezone, languages, max_autonomy)
values ('beautybay', 'Beauty Bay', 'Europe/London', '{en,fr}', 'N3')
on conflict (code) do nothing;

-- ----------------------------- Politique -------------------------------------

insert into tenant_policies (tenant_id, business_hours, offhours_message,
  ticket_backend, kb_source, identity_source, trigger_mode)
select id,
  '{"mon":["09:00","18:00"],"tue":["09:00","18:00"],"wed":["09:00","18:00"],"thu":["09:00","18:00"],"fri":["09:00","18:00"]}'::jsonb,
  'Your request has been received. It will be processed during business hours.',
  'none', 'none', 'none', 'auto'
from tenants where code = 'beautybay'
on conflict (tenant_id) do nothing;

-- ----------------------------- Canal -----------------------------------------

-- Jira Beauty Bay : tous les tickets du site beautybay.atlassian.net
insert into channels (tenant_id, platform, external_id, display_name, status)
select id, 'jira', 'beautybay.atlassian.net', 'Beauty Bay — Jira', 'active'
from tenants where code = 'beautybay'
on conflict (platform, external_id) do nothing;
