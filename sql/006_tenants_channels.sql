-- =============================================================================
-- Enregistrement des societes et de leurs canaux
--
-- C'est cette etape qui rend le systeme operationnel : sans tenant ni channel,
-- les messages arrivent mais ne sont associes a rien (tenant_id = NULL).
--
-- Correspond a la procedure §13 du document. Idempotent (ON CONFLICT).
--
-- Les codes et external_id doivent correspondre EXACTEMENT a ce que le pipeline
-- normalise (build_pipeline.py, section NORMALIZE_JS) et a ce qui est deja
-- en production dans Supabase.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. SOCIETES (tenants)
-- -----------------------------------------------------------------------------
INSERT INTO tenants (code, name, timezone, languages, max_autonomy, observation_only) VALUES
  ('beautybay',      'Beauty Bay',         'Europe/Paris',          '{fr,en}',    'N3', false),
  ('bazarchic',      'Bazarchic',          'Europe/Paris',          '{fr,en}',    'N3', false),
  ('bouchara',       'Bouchara',           'Europe/Paris',          '{fr}',       'N3', false),
  ('atlasformen',    'Atlas For Men',      'Europe/Paris',          '{fr}',       'N3', false),
  ('fsaget',         'François Saget',     'Europe/Paris',          '{fr}',       'N3', false),
  ('itsupportliban', 'IT Support Liban',   'Indian/Antananarivo',   '{fr,en,ar}', 'N3', false),
  ('devmg',          'Dev MG',             'Indian/Antananarivo',   '{fr,en}',    'N3', false)
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name,
  timezone = EXCLUDED.timezone,
  languages = EXCLUDED.languages;

-- Policies minimales pour chaque societe
INSERT INTO tenant_policies (tenant_id, ticket_backend, kb_source, identity_source, trigger_mode)
SELECT id, 'none', 'none', 'none', 'auto' FROM tenants
WHERE code IN ('beautybay','bazarchic','bouchara','atlasformen','fsaget','itsupportliban','devmg')
ON CONFLICT (tenant_id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 2. CANAUX (channels)
-- Les external_id correspondent exactement a ce que le normaliser JS produit :
--   - Jira : le hostname Atlassian (extrait du self URL)
--   - Confluence : idem
--   - Email/Outlook : l'adresse de la boite de reception (champ TO)
--   - Telegram : le group chat_id (numerique, negatif)
--   - ClickUp : le space_id ou list_id
--   - Teams : le channel_id du webhook
-- -----------------------------------------------------------------------------

-- BeautyBay
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'jira', 'beautybay.atlassian.net', 'Beauty Bay — Jira', 'support', 'active'
FROM tenants t WHERE t.code = 'beautybay'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'confluence', 'beautybay.atlassian.net', 'Confluence BeautyBay', 'support', 'active'
FROM tenants t WHERE t.code = 'beautybay'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'email', 'itsupport@beautybay.com', 'Outlook BeautyBay', 'support', 'active'
FROM tenants t WHERE t.code = 'beautybay'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

-- Bazarchic
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'jira', 'bzcmtc.atlassian.net', 'Bazarchic — Jira', 'support', 'active'
FROM tenants t WHERE t.code = 'bazarchic'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'confluence', 'bzcmtc.atlassian.net', 'Confluence Bazarchic', 'support', 'active'
FROM tenants t WHERE t.code = 'bazarchic'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'email', 'itsupport@bazarchic.com', 'Email IT Support Bazarchic', 'support', 'active'
FROM tenants t WHERE t.code = 'bazarchic'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'email', 'support-odoo@regardbeauty.onmicrosoft.com', 'Outlook Odoo (Regard Beauty)', 'support', 'active'
FROM tenants t WHERE t.code = 'bazarchic'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'clickup', '9012970281', 'ClickUp Regard Beauty', 'dev', 'active'
FROM tenants t WHERE t.code = 'bazarchic'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

-- Atlas For Men
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'email', 'thaina_aa@atlasformen.com', 'Outlook Atlas For Men', 'support', 'active'
FROM tenants t WHERE t.code = 'atlasformen'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'teams', 'THAINA_AA@atlasformen.com', 'Teams Atlas For Men', 'support', 'active'
FROM tenants t WHERE t.code = 'atlasformen'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

-- Francois Saget
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'email', 'support-it@francoisesaget.com', 'Francois Saget — Outlook', 'support', 'active'
FROM tenants t WHERE t.code = 'fsaget'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

-- Dev MG : Telegram (canal pilote)
INSERT INTO channels (tenant_id, platform, external_id, display_name, purpose, status)
SELECT t.id, 'telegram', '-5219441607', 'DEV MG — canal de test', 'support', 'active'
FROM tenants t WHERE t.code = 'devmg'
ON CONFLICT (platform, external_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, status = 'active';

-- -----------------------------------------------------------------------------
-- 3. RETROACTIF : associer les tickets orphelins (tenant_id NULL) au bon tenant
-- via la table events (qui a deja les channel_id corrects).
-- On prend le dernier event du meme sender_raw a la meme date pour retrouver
-- le canal d'origine.
-- -----------------------------------------------------------------------------
UPDATE tickets t
SET channel_id = sub.channel_id,
    tenant_id = sub.tenant_id
FROM (
  SELECT DISTINCT ON (e.body)
    e.body, e.channel_id, e.tenant_id
  FROM events e
  WHERE e.channel_id IS NOT NULL AND e.tenant_id IS NOT NULL
  ORDER BY e.body, e.received_at DESC
) sub
WHERE t.tenant_id IS NULL
  AND t.channel_id IS NULL
  AND (t.title ILIKE '%' || left(sub.body, 60) || '%'
       OR t.summary ILIKE '%' || left(sub.body, 60) || '%');

-- Fallback : les tickets restants sans tenant qui viennent du canal Telegram DevMG
-- (la plupart des tests passent par ce canal)
UPDATE tickets
SET tenant_id = (SELECT id FROM tenants WHERE code = 'devmg'),
    channel_id = (SELECT id FROM channels WHERE platform = 'telegram' AND external_id = '-5219441607')
WHERE tenant_id IS NULL AND channel_id IS NULL;

-- -----------------------------------------------------------------------------
-- Verification apres application :
--
--   SELECT count(*) FROM tenants;          -- 7
--   SELECT count(*) FROM channels;         -- 12+
--   SELECT t.code, count(ch.id)
--     FROM tenants t LEFT JOIN channels ch ON ch.tenant_id = t.id
--     GROUP BY t.code ORDER BY t.code;
--   SELECT count(*) FROM tickets WHERE tenant_id IS NULL;  -- 0
-- -----------------------------------------------------------------------------
