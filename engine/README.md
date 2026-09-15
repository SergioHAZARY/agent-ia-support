# Moteur de triage

La logique de **WF-03**, sous forme Python reutilisable et testable. Le modele
propose un JSON de triage ; le code applique les garde-fous et decide de la suite.

| Fichier | Role | Testable sans cle API ? |
|---|---|---|
| `guardrails.py` | Les verifications appliquees APRES la reponse du modele (regle 5) | **Oui** |
| `test_guardrails.py` | 24 tests des garde-fous | **Oui** |
| `triage.py` | Assemble le contexte, appelle Claude en sortie structuree, applique les garde-fous | L'appel non ; le chargement oui |
| `requirements.txt` | `anthropic` pour l'appel reel | |

## Ce que verifient les garde-fous

Aucune de ces regles ne depend du modele -- elles s'executent en Python :

- confiance sous 0,7 -> N3
- niveau propose au-dela du plafond de la societe -> N3
- demandeur non verifie demandant un N1 -> N3
- runbook inconnu, ou role hors `allowed_roles` -> N3
- action pour un tiers sans role manager/it/admin -> N3
- categorie hors taxonomie -> corrigee + alerte
- interdits permanents (2FA, comptes a privileges, suppression, prod, RDP...) -> N3 + alerte
- palier P0 -> ticket cree, rien publie

## Lancer les tests (maintenant, sans rien d'autre)

```bash
cd engine
python test_guardrails.py
```

Attendu : `24 ok, 0 echec(s)`.

## Appel reel (quand une cle API est disponible)

```bash
pip install -r requirements.txt
set ANTHROPIC_API_KEY=sk-ant-...        # Windows ; export sous Linux
set ANTHROPIC_MODEL=claude-sonnet-5

python -c "from triage import *; from guardrails import *; \
  ctx=TriageContext(max_autonomy='N3'); \
  print(run_triage(build_user_content('mon vpn ne marche plus', requester='INCONNU', channel='Telegram'), ctx))"
```

## Comment ca se branche dans n8n

Deux options, decrites dans `../tools/n8n-agent.md` :

- **Chaine deterministe (pilote)** : un noeud Function n8n porte cette meme
  logique, ou appelle ce moteur expose en petit service.
- **Noeud AI Agent** : plus tard, quand la taxonomie est stable.

Dans les deux cas, `apply_guardrails` reste la barriere de securite, en code,
apres la reponse du modele.

## Etat

`guardrails.py` : ecrit et **teste** (24/24). `triage.py` : ecrit, chargement du
prompt et assemblage du contexte verifies ; l'appel a l'API reste a valider avec
une vraie cle.
