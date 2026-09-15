# Le serveur : ce que c'est, pourquoi il faut en un, comment l'installer

Procédure complète, de la commande du serveur au premier démarrage. Comptez
une heure la première fois.

---

## 1. De quoi on parle

Une « VM » — machine virtuelle — c'est simplement **un ordinateur Linux qui
tourne en permanence et qu'on pilote à distance**, en ligne de commande. Pas un
écran, pas de souris : on s'y connecte en SSH depuis son poste.

Ce serveur hébergera cinq programmes, tous dans Docker :

| Programme | Rôle | Joignable depuis Internet ? |
|---|---|---|
| **n8n** | Écoute les canaux, exécute les actions | Oui, via Caddy |
| **PostgreSQL** | Tickets, configuration, journal d'audit | Non |
| **Qdrant** | Recherche dans la base de connaissances | Non |
| **Caddy** | Certificat HTTPS, protection de l'éditeur | Oui, ports 80 et 443 |
| **backup** | Sauvegarde quotidienne | Non |

Seul Caddy est exposé. PostgreSQL et Qdrant sont sur un réseau Docker interne,
inatteignable depuis l'extérieur — c'est configuré dans `docker-compose.yml`.

## 2. Pourquoi un serveur, et pas un poste de travail

**Telegram doit pouvoir appeler votre système.** Le fonctionnement est inversé
par rapport à ce qu'on imagine : ce n'est pas vous qui interrogez Telegram,
c'est Telegram qui envoie une requête HTTPS vers votre adresse à chaque message.
Il faut donc une adresse publique, joignable en permanence, avec un certificat
valide. Teams et Google Chat fonctionnent pareil, et sont encore plus stricts.

**Le travail continue quand personne ne regarde.** Un message à 23 h doit créer
un ticket. L'indexation de la base de connaissances tourne la nuit. La file de
reprise réessaie après une panne réseau. Rien de tout cela ne survit à une mise
en veille.

**Les données doivent être sauvegardées.** Le journal d'audit doit être
opposable : « qui a validé la création de ce compte ». Sur un poste de travail,
il disparaît au premier reformatage.

## 3. Choisir où le mettre

La question déterminante n'est pas le prix, c'est **votre Active Directory**.

### Cas A — vous démarrez en triage (palier P1)

L'agent lit, classe, crée les tickets, escalade. Il n'écrit dans aucun système
technique. C'est le périmètre des quatre premières semaines.

→ **N'importe quel VPS convient.** C'est le choix recommandé pour démarrer :
prêt en cinq minutes, aucune demande à faire à la DSI.

### Cas B — l'agent doit écrire dans l'AD sur site

Créer un compte, réinitialiser un mot de passe. C'est le palier P3, pas avant
la semaine 3, et seulement si vous le décidez.

Un serveur loué chez un hébergeur **ne peut pas joindre votre annuaire
interne** : il est de l'autre côté du pare-feu. Trois solutions, par ordre de
préférence :

1. **Un micro-service interne** sur un serveur joint au domaine, exposant une
   petite API (`POST /users`, `POST /users/{id}/reset-password`), appelé par le
   VPS à travers un tunnel WireGuard. Le VPS reste dehors, seule une API
   minuscule et contrôlée est exposée. C'est propre et ça isole les droits
2. **Mettre la VM sur le réseau interne**, avec une redirection de port pour
   les webhooks entrants. Demande une intervention de la DSI
3. **Microsoft Graph** — uniquement si l'AD sur site n'est plus la source de
   vérité. En environnement hybride, écrire dans le cloud est écrasé à la
   synchronisation suivante

**Vous n'avez pas à trancher maintenant.** Démarrez en cas A, migrez si le
besoin se confirme. Le `docker-compose.yml` est identique dans les deux cas.

## 4. Commander le serveur

### Caractéristiques

| | Minimum | Confortable |
|---|---|---|
| Processeur | 2 vCPU | 4 vCPU |
| Mémoire | 4 Go | 8 Go |
| Disque | 40 Go SSD | 80 Go SSD |
| Système | **Ubuntu Server 24.04 LTS** | idem |

Ubuntu 24.04 LTS : support jusqu'en 2029, et c'est la distribution la mieux
documentée pour Docker. Ne prenez pas une version non-LTS.

### Hébergeurs

| Hébergeur | Offre indicative | Remarque |
|---|---|---|
| **Hetzner** | CX32 — 4 vCPU, 8 Go, ~8 €/mois | Le meilleur rapport qualité-prix. Datacenters en Allemagne et Finlande |
| **Scaleway** | DEV1-M — ~15 €/mois | Français, datacenter à Paris |
| **OVH** | VPS Comfort — ~15 €/mois | Français, souvent déjà référencé en entreprise |
| **Infomaniak** | ~20 €/mois | Suisse, argument RGPD |

Pour des entités françaises soumises au RGPD, un hébergeur européen évite une
discussion sur les transferts de données. Vérifiez si votre entreprise a déjà
un contrat : cela évite un passage aux achats.

### À la commande

- Choisir **Ubuntu Server 24.04 LTS**
- Coller votre **clé SSH publique** (voir ci-dessous). Ne jamais activer
  l'authentification par mot de passe
- Noter l'**adresse IPv4** attribuée

Si vous n'avez pas encore de clé SSH, sur votre poste Windows (Git Bash ou
PowerShell) :

```bash
ssh-keygen -t ed25519 -C "agent-support"
cat ~/.ssh/id_ed25519.pub     # c'est cette ligne qu'on colle chez l'hébergeur
```

Le fichier sans `.pub` est votre clé privée : elle ne quitte jamais votre poste.

## 5. Le nom de domaine

**Obligatoire, et c'est la première chose à faire** — la propagation DNS prend
parfois quelques heures, autant la lancer tôt.

Chez votre registraire, créez un enregistrement :

```
Type : A
Nom  : support          (donne support.votredomaine.com)
Cible: 203.0.113.42     (l'IPv4 du serveur)
TTL  : 300
```

Vérifier depuis votre poste :

```bash
nslookup support.votredomaine.com
```

Tant que cette commande ne retourne pas l'IP du serveur, inutile d'aller plus
loin : Caddy ne pourra pas obtenir de certificat.

## 6. Première connexion et durcissement

```bash
ssh root@203.0.113.42
```

### Mises à jour et fuseau

```bash
apt update && apt upgrade -y
timedatectl set-timezone Europe/Paris
```

### Un utilisateur non-root

On ne travaille jamais en root au quotidien.

```bash
adduser --gecos "" agent
usermod -aG sudo agent
rsync --archive --chown=agent:agent ~/.ssh /home/agent
```

### Interdire la connexion root et les mots de passe

```bash
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

**Avant de fermer cette session**, ouvrez un second terminal et vérifiez que
`ssh agent@203.0.113.42` fonctionne. Si vous fermez sans vérifier et que la clé
n'a pas été copiée, vous perdez l'accès au serveur.

### Pare-feu

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```

Le port 80 est **obligatoire** : Let's Encrypt s'en sert pour vérifier que vous
contrôlez le domaine. Le fermer fait échouer l'émission du certificat.

### Mises à jour de sécurité automatiques

```bash
apt install -y unattended-upgrades fail2ban
dpkg-reconfigure -plow unattended-upgrades
```

`fail2ban` bannit les IP qui tentent des connexions SSH en force brute. Sur un
serveur exposé, ces tentatives commencent dans l'heure qui suit la mise en
ligne — ce n'est pas de la paranoïa.

## 7. Docker

Reprenez la session avec l'utilisateur `agent` :

```bash
ssh agent@203.0.113.42

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

docker --version
docker compose version
```

Les deux commandes doivent répondre. Le plugin Compose est inclus dans les
versions récentes : si `docker compose version` échoue, installez
`docker-compose-plugin`.

## 8. Déployer le projet

```bash
sudo mkdir -p /opt/agent-support && sudo chown $USER: /opt/agent-support
cd /opt/agent-support
```

Copiez-y le contenu du projet — par `git clone` si vous l'avez mis sur un dépôt,
sinon depuis votre poste :

```bash
# depuis votre poste Windows, dans le dossier du projet
scp -r docker-compose.yml Caddyfile .env.example sql prompts tools n8n \
    agent@203.0.113.42:/opt/agent-support/
```

Puis, sur le serveur :

```bash
cd /opt/agent-support
cp .env.example .env

# Le mot de passe de l'éditeur doit être un hash bcrypt, pas du texte
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'votre-mot-de-passe'

openssl rand -base64 24    # POSTGRES_PASSWORD
openssl rand -hex 32       # N8N_ENCRYPTION_KEY

nano .env                  # coller les valeurs, renseigner N8N_HOST et la clé Anthropic
chmod 600 .env
```

Puis démarrer :

```bash
docker compose up -d
docker compose logs -f postgres     # attendre « base agent prête », puis Ctrl+C
```

### Vérifier

```bash
docker compose ps           # les 5 services en « running »

docker compose exec postgres psql -U agent -d agent -c \
  "select count(*) as taxonomie from taxonomy;
   select count(*) as runbooks from runbooks;
   select count(*) as societes from tenants;"
```

Attendu : 36 lignes de taxonomie, 27 runbooks, 0 société.

Ouvrez enfin `https://support.votredomaine.com` dans un navigateur. Caddy
demande l'identifiant et le mot de passe définis dans `.env`, puis n8n propose
de créer le compte propriétaire. Le cadenas doit être vert : si le navigateur
signale un certificat invalide, c'est que le DNS n'était pas encore propagé au
démarrage — `docker compose restart caddy` après vérification du DNS.

## 9. Sauvegardes

Le service `backup` écrit chaque jour dans `/opt/agent-support/backups/`.
**Ces fichiers sont sur le même serveur : ce n'est pas encore une sauvegarde.**
Un serveur perdu, c'est les dumps perdus avec.

Copie quotidienne vers votre poste ou un stockage objet :

```bash
# depuis votre poste
rsync -avz agent@203.0.113.42:/opt/agent-support/backups/ ./backups-distants/
```

Et surtout : **conservez `N8N_ENCRYPTION_KEY` ailleurs que sur la VM**, dans un
gestionnaire de mots de passe. Sans cette clé, les credentials contenues dans
une sauvegarde sont définitivement illisibles — vous auriez les données mais
plus aucun accès aux systèmes.

Testez une restauration avant de considérer que la sauvegarde fonctionne :

```bash
gunzip -c backups/agent-20260915-0300.sql.gz \
  | docker compose exec -T postgres psql -U agent -d agent
```

## 10. Deux environnements

Le document prévoit un environnement de test séparé, et ce n'est pas du luxe :
**aucun runbook ne doit jamais être testé pour la première fois en production**.

Le plus simple est un second serveur identique, plus petit (2 vCPU, 4 Go),
sur un sous-domaine `support-test.votredomaine.com`, avec son propre groupe
Telegram et des identités fictives. Environ 8 €/mois de plus.

## Récapitulatif des coûts

| Poste | Mensuel |
|---|---|
| Serveur de production | 8 à 25 € |
| Serveur de test | 5 à 10 € |
| Nom de domaine | ~1 € |
| API Claude | 0,01 à 0,03 € par ticket, soit ~30 € pour 1 000 tickets |
| **Total pour démarrer** | **environ 50 €/mois** |

Jira Service Management, le gestionnaire de secrets et les licences éventuelles
s'ajoutent plus tard, et pèsent bien plus lourd que l'infrastructure.
