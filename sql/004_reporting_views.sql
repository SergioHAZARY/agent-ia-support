-- =============================================================================
-- Vues de reporting supplémentaires — facilitent les questions en langage
-- naturel posées via le connecteur Supabase depuis le projet Claude.
-- =============================================================================

-- Répartition par catégorie / sous-catégorie (« quelles demandes reviennent ? »)
create or replace view v_stats_categories as
select coalesce(tn.code, '(inconnu)')     as societe,
       t.category,
       t.subcategory,
       count(*)                            as total,
       count(*) filter (where t.status not in ('resolu','clos','rejete')) as ouverts,
       count(*) filter (where t.autonomy_level = 'N3')                    as escalades
from tickets t
left join tenants tn on tn.id = t.tenant_id
group by 1, 2, 3
order by total desc;

-- Activité des 30 derniers jours, par jour et par société
create or replace view v_tickets_recents as
select coalesce(tn.code, '(inconnu)')      as societe,
       date_trunc('day', t.created_at)::date as jour,
       count(*)                             as crees,
       count(*) filter (where t.resolved_at is not null) as resolus,
       count(*) filter (where t.autonomy_level in ('N0','N1')) as traites_par_agent
from tickets t
left join tenants tn on tn.id = t.tenant_id
where t.created_at >= now() - interval '30 days'
group by 1, 2
order by jour desc, societe;

comment on view v_stats_categories is
  'Reporting : volume par catégorie/sous-catégorie, par société.';
comment on view v_tickets_recents is
  'Reporting : activité des 30 derniers jours, par jour et société.';
