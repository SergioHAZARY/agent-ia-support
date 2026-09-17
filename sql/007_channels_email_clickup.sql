-- Canaux supplémentaires : Email Bazarchic + ClickUp Regard Beauty
-- À appliquer après 001_schema.sql

-- Canal email pour Bazarchic (itsupport@bazarchic.com)
INSERT INTO channels (tenant_id, platform, external_id, display_name, status)
SELECT t.id, 'email', 'itsupport@bazarchic.com', 'Email IT Support Bazarchic', 'active'
FROM tenants t WHERE t.code = 'bazarchic'
ON CONFLICT (platform, external_id) DO NOTHING;

-- Canal ClickUp pour Regard Beauty (workspace 9012970281)
-- Rattaché au tenant bazarchic — les tâches portent le département dans les tags
INSERT INTO channels (tenant_id, platform, external_id, display_name, status)
SELECT t.id, 'clickup', '9012970281', 'ClickUp Regard Beauty', 'active'
FROM tenants t WHERE t.code = 'bazarchic'
ON CONFLICT (platform, external_id) DO NOTHING;
