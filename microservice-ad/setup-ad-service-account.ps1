# setup-ad-service-account.ps1
# ---------------------------------------------------------------------------
# A lancer UNE FOIS, par un administrateur, sur une machine qui possede le
# module ActiveDirectory (RSAT) : le controleur de domaine lui-meme, ou un
# poste d'administration. Le bastion 13.39.175.150 n'a PAS RSAT -- ce n'est
# pas grave, le micro-service n'en a pas besoin, seul CE script en a besoin.
#
# Prepare le strict necessaire, sans un iota de droit de plus :
#   1. une OU dediee "Agent" ou les comptes geres par l'agent seront crees ;
#   2. un compte de service svc-agent, utilisateur ordinaire, NON admin ;
#   3. une delegation MINIMALE : creer/desactiver des utilisateurs et
#      reinitialiser les mots de passe, UNIQUEMENT dans cette OU.
#
# Ne touche a aucun compte existant ni a aucun groupe a privileges.
# Relisez les parametres avant d'executer.
#
# Fichier en ASCII pur (pas d'accents).

[CmdletBinding()]
param(
    [string]$OUName   = "Agent",
    [string]$ParentOU = "OU=Utilisateurs,DC=FSAGET,DC=PRI",
    [string]$SvcSam   = "svc-agent",
    [string]$SvcName  = "Service Agent IA Support"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Module -ListAvailable -Name ActiveDirectory)) {
    Write-Host "Le module ActiveDirectory (RSAT) est absent de cette machine." -ForegroundColor Yellow
    Write-Host "Lancez ce script sur le controleur de domaine FSAGET.PRI, ou sur"
    Write-Host "un poste d'administration disposant de RSAT AD DS Tools."
    Write-Host ""
    Write-Host "Le bastion n'a pas besoin de RSAT : il ne fait tourner que le"
    Write-Host "micro-service Python, qui parle a l'AD en LDAPS."
    exit 1
}
Import-Module ActiveDirectory

Write-Host "== 1. OU dediee ==" -ForegroundColor Cyan
$ouDN = "OU=$OUName,$ParentOU"
if (-not (Get-ADOrganizationalUnit -Filter "distinguishedName -eq '$ouDN'" -ErrorAction SilentlyContinue)) {
    New-ADOrganizationalUnit -Name $OUName -Path $ParentOU -ProtectedFromAccidentalDeletion $true
    Write-Host "  OU creee : $ouDN"
} else {
    Write-Host "  OU deja presente : $ouDN"
}

Write-Host "== 2. Compte de service ==" -ForegroundColor Cyan
$svcUPN = "$SvcSam@$((Get-ADDomain).DNSRoot)"
if (-not (Get-ADUser -Filter "sAMAccountName -eq '$SvcSam'" -ErrorAction SilentlyContinue)) {
    $pwd = Read-Host "  Mot de passe pour $SvcSam (fort, a coller ensuite dans .env)" -AsSecureString
    New-ADUser -Name $SvcName -SamAccountName $SvcSam -UserPrincipalName $svcUPN `
        -AccountPassword $pwd -Enabled $true -PasswordNeverExpires $true `
        -CannotChangePassword $false -Path $ParentOU `
        -Description "Compte de service du micro-service AD. Droits delegues minimaux. NE PAS elever."
    Write-Host "  Compte cree : $svcUPN"
} else {
    Write-Host "  Compte deja present : $svcUPN"
}
$svc = Get-ADUser -Identity $SvcSam

Write-Host "== 3. Delegation minimale sur l'OU ==" -ForegroundColor Cyan
$acl = Get-Acl "AD:$ouDN"
$svcSid = New-Object System.Security.Principal.SecurityIdentifier $svc.SID

$userObjType   = [GUID]"bf967aba-0de6-11d0-a285-00aa003049e2"   # classe user
$resetPwdRight = [GUID]"00299570-246d-11d0-a768-00aa006e0529"   # extended right : reset password
$allProps      = [GUID]"00000000-0000-0000-0000-000000000000"

$rules = @(
    New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
        $svcSid, "CreateChild,DeleteChild", "Allow", $userObjType, "All"),
    New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
        $svcSid, "ReadProperty,WriteProperty", "Allow", $allProps, "Descendents", $userObjType),
    New-Object System.DirectoryServices.ActiveDirectoryAccessRule(
        $svcSid, "ExtendedRight", "Allow", $resetPwdRight, "Descendents", $userObjType)
)
foreach ($r in $rules) { $acl.AddAccessRule($r) }
Set-Acl "AD:$ouDN" $acl
Write-Host "  Delegation appliquee sur $ouDN"

Write-Host ""
Write-Host "Termine." -ForegroundColor Green
Write-Host "A reporter dans microservice-ad\.env :" -ForegroundColor Yellow
Write-Host "  AD_BIND_USER=$svcUPN"
Write-Host "  ALLOWED_OUS=$ouDN"
Write-Host "  AD_BASE_DN=$((Get-ADDomain).DistinguishedName)"
