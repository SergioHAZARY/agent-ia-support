-- =============================================================================
-- Runbooks v2 — enrichissement du catalogue
--
-- Ajoute 10 runbooks N2 (guider) couvrant les demandes les plus courantes
-- qui manquaient au catalogue initial, plus 5 entrées de taxonomie.
--
-- Memes principes que 003_runbooks.sql :
--   - N2 = guider, aucun backend, disponible partout des le palier P1
--   - ON CONFLICT DO NOTHING : idempotent, peut etre rejoue sans risque
--   - Aucun runbook ne contourne les interdits permanents (§10)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Taxonomie : categories manquantes
-- -----------------------------------------------------------------------------
insert into taxonomy (category, subcategory, description) values
  ('telephonie', 'fixe',             'Telephone fixe, standard, SVI'),
  ('telephonie', 'mobile',           'Telephone portable professionnel'),
  ('telephonie', 'visioconference',  'Teams, Zoom, Meet — problemes audio/video'),
  ('sauvegarde', 'restauration',     'Restaurer un fichier ou une version anterieure'),
  ('sauvegarde', 'politique',        'Questions sur la frequence, retention, perimetre')
on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N2 — RESEAU
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, execution_mode,
   approval_required, allowed_roles, preconditions)
values
  (null, 'GUIDE_VPN_DIAGNOSTIC',
   'Diagnostiquer un probleme de connexion VPN',
   'reseau', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Le client VPN est installe. Procedure : 1) Verifier la connexion Internet '
   '(ouvrir un site public). 2) Fermer et relancer le client VPN. '
   '3) Essayer un autre serveur VPN si disponible. 4) Verifier que le pare-feu '
   'ne bloque pas le port du VPN (UDP 1194 ou TCP 443 selon la config). '
   '5) Redemarrer le poste. Si le probleme persiste apres ces etapes, escalader '
   'en N3 avec le message d''erreur exact.')
on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N2 — POSTE DE TRAVAIL
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, execution_mode,
   approval_required, allowed_roles, preconditions)
values
  (null, 'GUIDE_IMPRIMANTE_INSTALL',
   'Installer une imprimante reseau',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'L''imprimante est sur le reseau et allumee. Procedure : '
   '1) Parametres > Peripheriques > Imprimantes. 2) Ajouter une imprimante. '
   '3) Choisir « L''imprimante souhaitee n''est pas dans la liste ». '
   '4) Entrer l''adresse IP ou le nom reseau fourni par l''IT. '
   '5) Installer le pilote propose ou telecharger depuis le site du fabricant. '
   '6) Imprimer une page de test.'),

  (null, 'GUIDE_TEAMS_PERIPHERIQUES',
   'Configurer les peripheriques audio/video dans Teams',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Procedure : 1) Teams > Parametres > Peripheriques. '
   '2) Selectionner le micro, les haut-parleurs et la camera souhaites. '
   '3) Cliquer « Passer un appel test » pour verifier. '
   '4) Si un peripherique n''apparait pas : verifier qu''il est branche, '
   'redemarrer Teams, verifier les autorisations Windows (Parametres > '
   'Confidentialite > Microphone / Camera).'),

  (null, 'GUIDE_ONEDRIVE_SYNC',
   'Resoudre les problemes de synchronisation OneDrive',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Procedure : 1) Verifier l''icone OneDrive dans la barre des taches '
   '(nuage bleu = OK, rouge = erreur, gris = deconnecte). '
   '2) Clic droit > Pause puis Reprendre la synchronisation. '
   '3) Si l''erreur persiste : Parametres OneDrive > Compte > Dissocier ce PC, '
   'puis reconfigurer. 4) Verifier l''espace de stockage disponible '
   '(quota OneDrive). 5) Si des fichiers sont en conflit, les renommer '
   'localement et laisser OneDrive resynchroniser.'),

  (null, 'GUIDE_ECRAN_MULTI',
   'Configurer un affichage multi-ecrans',
   'poste_travail', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Procedure : 1) Brancher le cable (HDMI, DisplayPort ou USB-C). '
   '2) Windows + P pour choisir le mode (Dupliquer, Etendre, Deuxieme ecran). '
   '3) Parametres > Systeme > Affichage pour reordonner les ecrans. '
   '4) Faire glisser les rectangles pour correspondre a la position physique. '
   '5) Definir la resolution et l''echelle pour chaque ecran.')

on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N2 — MESSAGERIE
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, execution_mode,
   approval_required, allowed_roles, preconditions)
values
  (null, 'GUIDE_OUTLOOK_PROFIL',
   'Recreer un profil Outlook corrompu',
   'messagerie', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Symptomes : Outlook ne demarre plus, se bloque au chargement, ou affiche '
   '« Impossible d''ouvrir votre dossier de messagerie ». '
   'Procedure : 1) Fermer Outlook completement. '
   '2) Panneau de configuration > Courrier > Afficher les profils. '
   '3) Supprimer le profil corrompu. 4) Ajouter un nouveau profil, '
   'entrer l''adresse email, laisser la decouverte automatique configurer. '
   '5) Redemarrer Outlook. Les emails se retelechargeront depuis le serveur.'),

  (null, 'GUIDE_SIGNATURE_EMAIL',
   'Mettre a jour sa signature email',
   'messagerie', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Procedure : 1) Outlook > Fichier > Options > Courrier > Signatures. '
   '2) Selectionner la signature a modifier ou en creer une nouvelle. '
   '3) Coller le modele de la societe (disponible dans la base de connaissances '
   'ou aupres du service communication). 4) Mettre a jour le nom, le poste '
   'et le numero de telephone. 5) Attribuer la signature aux nouveaux messages '
   'et aux reponses/transferts.'),

  (null, 'GUIDE_MOBILE_EMAIL',
   'Configurer l''email professionnel sur smartphone',
   'messagerie', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Procedure recommandee : utiliser l''app Outlook Mobile (iOS/Android). '
   '1) Installer Outlook depuis l''App Store ou le Play Store. '
   '2) Ajouter un compte > entrer l''adresse email professionnelle. '
   '3) Si l''entreprise utilise un MDM (Intune), accepter l''inscription '
   'au portail d''entreprise. 4) Configurer le code PIN de l''app. '
   'Alternative avec le client natif : Parametres > Comptes > Ajouter un '
   'compte > Exchange/Microsoft 365, memes identifiants.')

on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N2 — APPLICATIF
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, execution_mode,
   approval_required, allowed_roles, preconditions)
values
  (null, 'GUIDE_NAVIGATEUR_CACHE',
   'Vider le cache et les cookies du navigateur',
   'applicatif', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'A faire quand un site web se comporte anormalement, affiche une ancienne '
   'version, ou pose des problemes de connexion. '
   'Chrome/Edge : Ctrl+Shift+Suppr > cocher « Images et fichiers en cache » '
   'et « Cookies » > periode « Toutes les donnees » > Effacer. '
   'Firefox : Ctrl+Shift+Suppr > cocher « Cache » et « Cookies » > '
   'Intervalle « Tout » > Valider. '
   'ATTENTION : vider les cookies deconnecte de tous les sites.')

on conflict do nothing;

-- -----------------------------------------------------------------------------
-- N2 — SECURITE
-- -----------------------------------------------------------------------------
insert into runbooks
  (tenant_id, code, title, category, autonomy_level, execution_mode,
   approval_required, allowed_roles, preconditions)
values
  (null, 'GUIDE_PHISHING_ANALYSE',
   'Analyser un email suspect et adopter les bons reflexes',
   'securite', 'N2', 'manual', false, '{employe,manager,it,admin}',
   'Les 5 reflexes : 1) Ne JAMAIS cliquer sur un lien ou ouvrir une piece '
   'jointe d''un email suspect. 2) Verifier l''adresse de l''expediteur '
   '(survoler, pas seulement le nom affiche). 3) Se mefier des demandes '
   'urgentes (« votre compte sera ferme dans 24h »). 4) Verifier les fautes '
   'd''orthographe et les URLs suspectes (survoler sans cliquer). '
   '5) Signaler l''email a l''IT en le transferant EN PIECE JOINTE '
   '(pas un simple transfert). Ne jamais repondre a l''email suspect.')

on conflict do nothing;

-- -----------------------------------------------------------------------------
-- Verification apres application :
--
--   select count(*) from taxonomy;           -- 41 (36 + 5 nouvelles)
--   select count(*) from runbooks;           -- 35 (5 seed + 20 v1 + 10 v2)
--   select category, count(*) from runbooks
--     group by category order by category;   -- repartition par categorie
-- -----------------------------------------------------------------------------
