<#
.SYNOPSIS  Arm every page, after at least one clean week on the canary page.
.DESCRIPTION
    Deliberately separate from arm_page.ps1 and deliberately interactive: five
    accounts starting together is the failure mode this whole arming mechanism
    exists to prevent. -Force skips the prompt for unattended use.
.EXAMPLE  .\scripts\arm_all_pages.ps1
#>
[CmdletBinding()]
param([switch]$Force)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

& $python -m src.cli arming-status
if (-not $Force) {
    Write-Host "`nStai per armare TUTTE e cinque le pagine." -ForegroundColor Yellow
    Write-Host "Fallo solo dopo almeno una settimana pulita sulla pagina canary."
    $answer = Read-Host "Scrivi ARMA per procedere"
    if ($answer -cne 'ARMA') { Write-Host "Annullato." -ForegroundColor Green; exit 0 }
}
& $python -m src.cli arm-page all
exit $LASTEXITCODE
