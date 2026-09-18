-- =============================================================================
-- Migration 008 : canaux Confluence, Outlook supplémentaires et Teams Atlas For Men
--
-- Confluence Bazarchic         → bzcmtc.atlassian.net
-- Confluence BeautyBay         → beautybay.atlassian.net
-- Outlook Odoo (Regard Beauty) → support-odoo@regardbeauty.onmicrosoft.com  (tenant bazarchic)
-- Teams Atlas For Men          → THAINA_AA@atlasformen.com                   (nouveau tenant atlasformen)
-- Outlook BeautyBay            → itsupport@beautybay.com                     (tenant beautybay)
--
-- À appliquer après 007_channels_email_clickup.sql.
-- =============================================================================

-- ----------------------------- Nouveau tenant --------------------------------

-- Atlas For Men n'existait pas encore ; démarre en P1 comme les autres.
insert into tenants (code, name, timezone, languages, max_autonomy)
values ('atlasformen', 'Atlas For Men', 'Europe/Paris', '{fr}', 'N3')
on conflict (code) do nothing;

insert into tenant_policies (tenant_id, business_hours, offhours_message,
  ticket_backend, kb_source, identity_source, trigger_mode)
select id,
  '{"mon":["09:00","18:00"],"tue":["09:00","18:00"],"wed":["09:00","18:00"],"thu":["09:00","18:00"],"fri":["09:00","18:00"]}'::jsonb,
  'Votre demande a bien été reçue. Elle sera traitée aux heures ouvrables.',
  'none', 'none', 'none', 'auto'
from tenants where code = 'atlasformen'
on conflict (tenant_id) do nothing;

-- ----------------------------- Canaux ----------------------------------------

-- Confluence Bazarchic
insert into channels (tenant_id, platform, external_id, display_name, status)
select t.id, 'confluence', 'bzcmtc.atlassian.net', 'Confluence Bazarchic', 'active'
from tenants t where t.code = 'bazarchic'
on conflict (platform, external_id) do nothing;

-- Confluence BeautyBay
insert into channels (tenant_id, platform, external_id, display_name, status)
select t.id, 'confluence', 'beautybay.atlassian.net', 'Confluence BeautyBay', 'active'
from tenants t where t.code = 'beautybay'
on conflict (platform, external_id) do nothing;

-- Outlook Odoo — boîte Regard Beauty, rattachée au tenant bazarchic
insert into channels (tenant_id, platform, external_id, display_name, status)
select t.id, 'email', 'support-odoo@regardbeauty.onmicrosoft.com', 'Outlook Odoo (Regard Beauty)', 'active'
from tenants t where t.code = 'bazarchic'
on conflict (platform, external_id) do nothing;

-- Teams Atlas For Men
insert into channels (tenant_id, platform, external_id, display_name, status)
select t.id, 'teams', 'THAINA_AA@atlasformen.com', 'Teams Atlas For Men', 'active'
from tenants t where t.code = 'atlasformen'
on conflict (platform, external_id) do nothing;

-- Outlook BeautyBay
insert into channels (tenant_id, platform, external_id, display_name, status)
select t.id, 'email', 'itsupport@beautybay.com', 'Outlook BeautyBay', 'active'
from tenants t where t.code = 'beautybay'
on conflict (platform, external_id) do nothing;

-- ----------------------------- Vérification ----------------------------------
-- select code, name from tenants order by code;
-- select platform, external_id, display_name from channels order by platform, display_name;
