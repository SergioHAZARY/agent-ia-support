-- =============================================================================
-- Canaux multicanaux : tous les comptes a connecter.
--
-- Chaque ligne lie un canal (plateforme + identifiant externe) a une societe.
-- Le pipeline n8n resout la societe via cette table : un message entrant
-- dont le platform+external_id n'est pas ici est ignore (status='pending').
--
-- Les credentials sont des NOMS de credentials n8n, jamais des secrets.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Societes (tenants)
-- -----------------------------------------------------------------------------
INSERT INTO tenants (code, name, max_autonomy, observation_only)
VALUES
  ('bazarchic',   'Bazarchic',          'N3', false),
  ('beautybay',   'BeautyBay',          'N3', false),
  ('bouchara',    'Bouchara',           'N3', false),
  ('atlasformen', 'Atlas For Men',      'N3', true),
  ('fsaget',      'Francois Saget',     'N3', true),
  ('regardbeauty','Regard Beauty (IT)', 'N3', false),
  ('liban',       'IT Support Liban',   'N3', false)
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name,
  max_autonomy = EXCLUDED.max_autonomy;

-- -----------------------------------------------------------------------------
-- 2. Canaux : un adaptateur par plateforme, une ligne par compte.
--    external_id = identifiant unique du canal sur la plateforme :
--      - email : adresse de la boite de reception
--      - jira  : domaine Atlassian (site-url)
--      - clickup : list_id ou team_id
--      - telegram : chat_id du groupe
--      - teams : channel_id ou tenant/channel
--      - gchat : space name
-- -----------------------------------------------------------------------------

-- Bazarchic
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='bazarchic'),
   'jira', 'bzcmtc.atlassian.net',
   'Jira Bazarchic', 'support', 'active', 'Jira Bazarchic (HTTP Basic)'),

  -- Confluence utilise la meme API REST Atlassian que Jira (meme credential).
  -- Platform = 'jira' pour rester dans la contrainte du schema.
  ((SELECT id FROM tenants WHERE code='bazarchic'),
   'jira', 'bazarchic-confluence',
   'Confluence Bazarchic', 'support', 'active', 'Jira Bazarchic (HTTP Basic)'),

  ((SELECT id FROM tenants WHERE code='bazarchic'),
   'email', 'itsupport@bazarchic.com',
   'Email Groupe Chat BazarChic', 'support', 'active', 'IMAP Bazarchic')
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name,
  status = EXCLUDED.status,
  credential_ref = EXCLUDED.credential_ref;

-- BeautyBay
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='beautybay'),
   'jira', 'beautybay.atlassian.net',
   'Jira BeautyBay', 'support', 'active', 'Jira BeautyBay (HTTP Basic)'),

  ((SELECT id FROM tenants WHERE code='beautybay'),
   'jira', 'beautybay-confluence',
   'Confluence BeautyBay', 'support', 'active', 'Jira BeautyBay (HTTP Basic)'),

  ((SELECT id FROM tenants WHERE code='beautybay'),
   'email', 'itsupport@beautybay.com',
   'Outlook BeautyBay', 'support', 'active', 'IMAP BeautyBay'),

  ((SELECT id FROM tenants WHERE code='beautybay'),
   'clickup', '901222267724',
   'ClickUp BeautyBay (Escalades)', 'dev', 'active', 'ClickUp API')
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name,
  status = EXCLUDED.status,
  credential_ref = EXCLUDED.credential_ref;

-- Atlas For Men
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='atlasformen'),
   'email', 'thaina_aa@atlasformen.com',
   'Outlook Atlas For Men', 'support', 'active', 'IMAP AtlasForMen')
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name,
  status = EXCLUDED.status,
  credential_ref = EXCLUDED.credential_ref;

-- Francois Saget
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='fsaget'),
   'email', 'support-it@francoisesaget.com',
   'Outlook Francois Saget', 'support', 'active', 'IMAP FrancoisSaget')
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name,
  status = EXCLUDED.status,
  credential_ref = EXCLUDED.credential_ref;

-- Regard Beauty (Odoo)
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='regardbeauty'),
   'email', 'support-odoo@regardbeauty.onmicrosoft.com',
   'Outlook Odoo (Regard Beauty)', 'support', 'active', 'IMAP RegardBeauty')
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name,
  status = EXCLUDED.status,
  credential_ref = EXCLUDED.credential_ref;

-- IT Support Liban (canal pilote Telegram)
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='liban'),
   'telegram', '-5219441607',
   'IT Support Liban (DEV MG)', 'support', 'active', 'Telegram Agent Support')
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name,
  status = EXCLUDED.status,
  credential_ref = EXCLUDED.credential_ref;

-- Bouchara (Teams - a configurer quand le canal Teams sera pret)
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='bouchara'),
   'teams', 'bouchara-support',
   'Teams Bouchara', 'support', 'pending', NULL)
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name;

-- Bazarchic (Google Chat - a configurer quand le canal sera pret)
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status, credential_ref)
VALUES
  ((SELECT id FROM tenants WHERE code='bazarchic'),
   'gchat', 'bazarchic-gchat',
   'Google Chat Bazarchic', 'support', 'pending', NULL)
ON CONFLICT (platform, external_id) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  display_name = EXCLUDED.display_name;

-- -----------------------------------------------------------------------------
-- Verification :
--   SELECT t.name, c.platform, c.external_id, c.display_name, c.status
--   FROM channels c JOIN tenants t ON t.id = c.tenant_id
--   ORDER BY t.name, c.platform;
-- -----------------------------------------------------------------------------
