# install-windows.ps1 -- installation NON INTRUSIVE du micro-service AD.
# ---------------------------------------------------------------------------
# A lancer DANS la session RDP, dans le dossier microservice-ad.
#
# Concu pour ne rien perturber :
#   - N'installe RIEN au niveau systeme. Tout vit dans un venv local .venv.
#   - N'utilise PAS le Python systeme au-dela de la creation du venv.
#   - Ecoute par defaut sur 127.0.0.1 : RIEN n'est expose au demarrage.
#   - Ne cree aucune regle de pare-feu, ne touche a aucun service, ne
#     redemarre rien.
#   - Reversible : uninstall-windows.ps1 retire tout.
#
# Le script fait un PRE-VOL en lecture seule et demande confirmation AVANT
# toute ecriture.
#
# NB : fichier volontairement en ASCII pur (pas d'accents) pour eviter les
# erreurs de parsing sous Windows PowerShell 5.1.

[CmdletBinding()]
param(
    [int]$Port      = 8080,
    [string]$Listen = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "================ PRE-VOL (lecture seule) ================" -ForegroundColor Cyan

# 1. Python present ?
$pyver = & python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Aucun Python trouve. Le script s'arrete sans rien installer." -ForegroundColor Yellow
    Write-Host "  Installez Python 3.11+ pour l'utilisateur courant, puis relancez."
    exit 1
}
Write-Host "  Python trouve : $pyver"

# 2. Le port est-il deja pris ?
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "  Le port $Port est DEJA utilise (PID $($busy.OwningProcess))." -ForegroundColor Yellow
    Write-Host "  Relancez avec un autre port : .\install-windows.ps1 -Port 8090"
    exit 1
}
Write-Host "  Port $Port libre."

# 3. Resume et confirmation
Write-Host ""
Write-Host "Ce script va, UNIQUEMENT dans $here :" -ForegroundColor Cyan
Write-Host "  - creer un environnement Python local .venv"
Write-Host "  - y installer fastapi, uvicorn, ldap3, pydantic"
Write-Host "  - NE PAS lancer le service (vous le lancerez a la main)"
Write-Host "  - NE PAS creer de regle de pare-feu, ne toucher a aucun service"
Write-Host ""
$ok = Read-Host "Continuer ? (oui/non)"
if ($ok -ne "oui") { Write-Host "Annule, rien n'a ete modifie."; exit 0 }

Write-Host "================ INSTALLATION ================" -ForegroundColor Cyan

if (-not (Test-Path ".venv")) {
    & python -m venv .venv
    Write-Host "  venv cree."
} else {
    Write-Host "  venv deja present, reutilise."
}

$vpy = Join-Path $here ".venv\Scripts\python.exe"
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r requirements.txt
Write-Host "  dependances installees."

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  .env cree depuis le modele -- A RENSEIGNER avant de lancer." -ForegroundColor Yellow
} else {
    Write-Host "  .env deja present, conserve."
}

Write-Host ""
Write-Host "Installe." -ForegroundColor Green
Write-Host "Etapes suivantes, a la main :" -ForegroundColor Cyan
Write-Host "  1. Renseigner .env : AD_BIND_PASS, API_TOKEN, ALLOWED_OUS"
Write-Host "  2. Test de sante, en loopback, sans rien exposer :"
Write-Host "       .\.venv\Scripts\uvicorn.exe main:app --host $Listen --port $Port"
Write-Host "     puis dans une autre fenetre :"
Write-Host "       curl.exe http://127.0.0.1:$Port/health"
Write-Host "  3. Si /health repond ok, on met en place le tunnel et le service."
Write-Host ""
Write-Host "Pour tout retirer : .\uninstall-windows.ps1"
