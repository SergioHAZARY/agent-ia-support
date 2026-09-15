# uninstall-windows.ps1 -- retire ce que install-windows.ps1 a pose, et RIEN
# d'autre. N'affecte aucun autre service ni le Python systeme.
# Fichier en ASCII pur.

[CmdletBinding()]
param([string]$ServiceName = "AgentAD")

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "Desinstallation du micro-service AD (local uniquement)." -ForegroundColor Cyan

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "  arret et suppression du service $ServiceName"
    try { nssm stop $ServiceName; nssm remove $ServiceName confirm } catch {
        Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe delete $ServiceName | Out-Null
    }
} else {
    Write-Host "  aucun service $ServiceName, rien a arreter."
}

if (Test-Path ".venv") {
    Remove-Item ".venv" -Recurse -Force
    Write-Host "  .venv supprime."
}

Write-Host ""
Write-Host "Retire. Le fichier .env (secrets) est CONSERVE volontairement." -ForegroundColor Green
Write-Host "Le supprimer si vous ne reinstallez pas : Remove-Item .env"
Write-Host "Aucun autre composant de la machine n'a ete touche."
