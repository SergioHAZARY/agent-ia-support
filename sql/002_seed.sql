-- =============================================================================
-- Seed de démarrage.
--
-- Ne contient AUCUNE société réelle : le système démarre vide, et les sociétés
-- s'ajoutent par la procédure de la section 13 (30 minutes, sans code).
-- Ce fichier ne pose que ce qui est vrai partout : la taxonomie de référence
-- et les runbooks globaux.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Taxonomie (annexe A). Sert à contraindre le champ `category` produit par le
-- modèle : une catégorie hors de cette liste est une erreur de triage.
-- À réviser après le test à froid de la semaine 2, sur les vrais messages.
-- -----------------------------------------------------------------------------
create table if not exists taxonomy (
  category      text not null,
  subcategory   text not null,
  description   text,
  primary key (category, subcategory)
);

insert into taxonomy (category, subcategory, description) values
  ('acces_licences', 'ajout_utilisateur',      'Ajouter une personne à un logiciel ou une licence'),
  ('acces_licences', 'retrait_utilisateur',    'Retirer un accès, libérer une licence'),
  ('acces_licences', 'changement_offre',       'Monter ou descendre de formule'),

  ('compte_identite', 'creation_compte',       'Nouvel arrivant, création dans l''annuaire'),
  ('compte_identite', 'mot_de_passe',          'Oubli, expiration, réinitialisation'),
  ('compte_identite', 'mfa',                   'Double authentification, changement de téléphone'),
  ('compte_identite', 'depart_collaborateur',  'Désactivation, transfert de données'),

  ('messagerie', 'creation_boite',             'Nouvelle boîte aux lettres'),
  ('messagerie', 'alias',                      'Adresse secondaire, redirection'),
  ('messagerie', 'liste_diffusion',            'Groupe de distribution'),
  ('messagerie', 'quota',                      'Boîte pleine, archivage'),
  ('messagerie', 'spam',                       'Courrier indésirable, faux positif'),

  ('poste_travail', 'materiel',                'Panne, remplacement, commande'),
  ('poste_travail', 'systeme',                 'Système d''exploitation, mises à jour'),
  ('poste_travail', 'logiciel',                'Installation, licence poste'),
  ('poste_travail', 'imprimante',              'Impression, scan'),
  ('poste_travail', 'peripherique',            'Écran, casque, dock'),

  ('reseau', 'vpn',                            'Accès distant chiffré'),
  ('reseau', 'wifi',                           'Connexion sans fil'),
  ('reseau', 'connectivite',                   'Coupure, lenteur réseau'),
  ('reseau', 'acces_distant',                  'RDP, bureau à distance'),

  ('applicatif', 'bug',                        'Comportement anormal d''une application'),
  ('applicatif', 'anomalie_donnees',           'Données fausses ou manquantes'),
  ('applicatif', 'lenteur',                    'Performance dégradée'),
  ('applicatif', 'indisponibilite',            'Service inaccessible'),

  ('developpement', 'demande_evolution',       'Nouvelle fonctionnalité'),
  ('developpement', 'correction',              'Correction de bug à planifier'),
  ('developpement', 'deploiement',             'Mise en production'),

  ('securite', 'phishing',                     'Courriel suspect signalé'),
  ('securite', 'incident',                     'Compromission avérée ou suspectée'),
  ('securite', 'demande_suspecte',             'Tentative d''ingénierie sociale'),

  ('information', 'question_procedure',        'Comment fait-on pour…'),
  ('information', 'demande_documentation',     'Où trouver…'),

  ('hors_perimetre', 'conversation',           'Discussion ordinaire, pas une demande'),
  ('hors_perimetre', 'remerciement',           'Merci, ok, bien reçu'),
  ('hors_perimetre', 'hors_sujet',             'Sans rapport avec le support')
on conflict do nothing;

-- -----------------------------------------------------------------------------
-- Runbooks globaux (tenant_id = null) : valables pour toute société.
--
-- Ce sont des runbooks N2 — l'agent GUIDE, il n'exécute rien. Ils ne demandent
-- aucun backend, donc ils fonctionnent dès le palier P1, le jour du branchement.
-- Les runbooks N1 (exécution réelle) sont spécifiques à chaque société : ils
-- s'ajoutent quand son backend est déclaré dans tenant_backends.
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, execution_mode,
   approval_required, allowed_roles, preconditions)
values
  (null, 'GUIDE_PASSWORD_SELF_RESET',
   'Réinitialiser soi-même son mot de passe',
   'compte_identite', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Le portail libre-service est accessible et le demandeur a encore une méthode de vérification valide.'),

  (null, 'GUIDE_VPN_CONNEXION',
   'Se connecter au VPN',
   'reseau', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Le client VPN est déjà installé sur le poste.'),

  (null, 'GUIDE_MAILBOX_QUOTA',
   'Libérer de l''espace dans sa boîte aux lettres',
   'messagerie', 'N2', 'manual', false, '{employe,manager,it,admin}',
   null),

  (null, 'GUIDE_PHISHING_SIGNALEMENT',
   'Signaler un courriel suspect',
   'securite', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Ne jamais demander au demandeur de transférer le courriel en pièce jointe cliquable.'),

  (null, 'GUIDE_IMPRIMANTE_FILE',
   'Débloquer une file d''impression',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}',
   null)
on conflict do nothing;

-- -----------------------------------------------------------------------------
-- Vérification rapide après application :
--
--   select count(*) from taxonomy;    -- 36
--   select count(*) from runbooks;    -- 5
--   select count(*) from tenants;     -- 0, c'est normal
-- -----------------------------------------------------------------------------
