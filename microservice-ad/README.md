# Micro-service Active Directory

Le seul composant du système qui parle au contrôleur de domaine `FSAGET.PRI`
(`192.168.25.230`). Il tourne sur une machine du réseau interne, jamais exposé
à Internet, et n8n l'appelle par une petite API.

```
n8n (serveur dédié)  ──WireGuard──▶  ce micro-service  ──LDAPS──▶  FSAGET.PRI
```

## Pourquoi ce composant existe

Ni le serveur n8n, ni le poste de travail (`192.168.60.150`), ni le hub VPN OVH
n'atteignent le DC (vérifié le 2026-09-15 : route par Internet, ports AD
filtrés). Seul un hôte atteint l'annuaire : le **bastion Windows `13.39.175.150`**,
celui par lequel l'AD est administré en RDP (`WRDP-3.rdp`).

**C'est donc là que ce micro-service doit être installé** : il est allumé en
permanence et il atteint le DC. Plutôt que d'ouvrir l'annuaire vers l'extérieur,
on pose sur ce bastion une API minuscule qui n'expose que quatre actions,
chacune adossée à un runbook.

| Endpoint | Runbook | Action AD |
|---|---|---|
| `POST /users` | `AD_CREATE_USER` | Crée un compte dans l'OU déléguée |
| `POST /users/reset-password` | `AD_RESET_PASSWORD` | Réinitialise, force le changement à la connexion |
| `POST /users/disable` | `AD_DISABLE_USER` | **Désactive** — jamais ne supprime |
| `POST /users/groups` | `AD_ADD_TO_GROUP` | Ajoute à un groupe non privilégié |
| `GET /health` | — | Vérifie que le DC répond |

## Les garde-fous, en code

La délégation AD est une barrière ; le code en est une seconde, indépendante :

- **Liste blanche d'OU** — toute écriture hors de `ALLOWED_OUS` est refusée avant
  même de contacter le DC.
- **Groupes à privilèges bloqués** — Domain Admins et consorts sont refusés, quel
  que soit l'appelant.
- **Jamais de suppression** — `disable` désactive le compte (interdit permanent).
- **Le mot de passe temporaire ne circule pas en clair dans un groupe** — il n'est
  renvoyé qu'à n8n, serveur-à-serveur, et WF-05 le remet en message privé.
- **Jeton bearer** comparé en temps constant ; le service n'écoute que sur l'IP
  du tunnel.

Le compte de service n'est **jamais** un administrateur de domaine. Le compte
`FSAGET\Administrateur` transmis pour le diagnostic ne doit pas servir ici — et
son mot de passe est à changer, il a transité par une conversation.

---

## Installation

### 1. Créer le compte de service et l'OU (une fois, par un admin)

Sur une machine jointe au domaine, PowerShell en administrateur :

```powershell
# Adapter ParentOU à votre arborescence avant de lancer
.\setup-ad-service-account.ps1 -ParentOU "OU=Utilisateurs,DC=FSAGET,DC=PRI"
```

Le script crée l'OU `Agent`, le compte `svc-agent` (utilisateur ordinaire), et
lui délègue **uniquement** la création/désactivation d'utilisateurs et la
réinitialisation de mot de passe **dans cette OU**. Il affiche à la fin les deux
lignes à reporter dans `.env`.

### 2. Installer le service

Sur la machine interne qui atteint le DC (votre poste pour démarrer) :

```bash
cd microservice-ad
python -m venv .venv
.venv/Scripts/activate          # Windows ; sous Linux : source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# renseigner AD_BIND_PASS (mot de passe de svc-agent), API_TOKEN, ALLOWED_OUS
```

Générer le jeton d'API :

```bash
openssl rand -hex 32            # à coller dans API_TOKEN, ici ET dans n8n
```

### 3. Vérifier la connexion au DC avant tout

```bash
# le poste atteint-il le DC en LDAPS ?
python - <<'PY'
import socket; s=socket.create_connection(("192.168.25.230",636),5); print("LDAPS joignable"); s.close()
PY
```

Si ce test échoue, inutile d'aller plus loin : la machine n'atteint pas le DC
sur le port 636. Vérifier le pare-feu du DC et la route réseau.

### 4. Lancer

```bash
# Écouter sur l'IP du tunnel WireGuard, PAS sur 0.0.0.0
uvicorn main:app --host 10.0.0.100 --port 8080
```

Test de santé depuis la même machine :

```bash
curl http://10.0.0.100:8080/health
# {"ok": true, "domaine": "FSAGET.PRI", "ou_autorisees": 1}
```

### 5. En faire un service qui redémarre tout seul

Sur votre poste, pour le pilote, `nssm` suffit :

```powershell
nssm install AgentAD "C:\...\microservice-ad\.venv\Scripts\uvicorn.exe" ^
     "main:app --host 10.0.0.100 --port 8080"
nssm set AgentAD AppDirectory "C:\...\microservice-ad"
nssm start AgentAD
```

**Rappel :** un poste de travail s'éteint. Pour la production, déplacer ce
service sur une machine qui reste allumée et qui est jointe au domaine.

---

## Relier n8n à ce service

Le bastion `13.39.175.150` a une IP publique, et le VPS de l'agent aussi. Deux
façons de les relier, de la plus sûre à la plus simple :

**Tunnel WireGuard dédié (recommandé).** Le micro-service n'écoute alors que sur
l'IP du tunnel, jamais sur l'interface publique.
1. Sur le VPS de l'agent, ajouter le bastion comme peer, IP de tunnel ex. `10.9.0.2`.
2. Sur le bastion, monter le tunnel vers le VPS.
3. `uvicorn --host 10.9.0.2 --port 8080`.
4. Dans n8n, les webhooks AD pointent sur `http://10.9.0.2:8080/...`.

**Pare-feu par IP (plus simple, acceptable).** Ouvrir le port 8080 du bastion
**uniquement** à l'IP du VPS (security group AWS), avec TLS et le jeton bearer.
Ne jamais laisser ce port ouvert au monde.

Dans les deux cas, l'entête `Authorization: Bearer <API_TOKEN>` est exigée. Ne
pas réutiliser le hub WireGuard OVH : il est en production et partagé.

---

## Correspondance avec les runbooks n8n

Dans la table `runbooks`, renseigner `n8n_webhook` pour chaque code AD. Le
sous-workflow n8n correspondant :

1. reçoit les paramètres validés (WF-05, après le clic d'approbation) ;
2. **revérifie** rôle et interdits — une carte a pu attendre des heures ;
3. appelle l'endpoint de ce service avec le jeton ;
4. sur `temp_password` dans la réponse : le pousse en **message privé** au
   demandeur vérifié, jamais dans le groupe, et journalise l'envoi ;
5. écrit le résultat dans `ticket_events`.

---

## État

Écrit, **non testé contre le DC réel** — ni Docker ni annuaire accessibles depuis
le poste de développement au moment de l'écriture. Syntaxe Python validée. Le
premier appel réel (`/health`) est le vrai test : il confirme d'un coup la route
réseau, le LDAPS, le compte de service et la délégation.
