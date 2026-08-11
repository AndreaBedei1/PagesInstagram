<#
.SYNOPSIS
    From a clean checkout to a machine ready for credentials.

.DESCRIPTION
    Installs dependencies, prepares the database, imports the corpus and runs
    every gate. Deliberately does not touch ICE_MODE and does not arm any page:
    those are decisions, not setup steps, and a setup script that quietly makes
    them is how five accounts start posting by accident.

.EXAMPLE
    .\scripts\setup_production.ps1
    .\scripts\setup_production.ps1 -SkipModel
#>
[CmdletBinding()]
param(
    [switch]$SkipModel,
    [switch]$SkipCorpus
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host "== Ambiente virtuale ==" -ForegroundColor Cyan
    uv venv .venv
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
}
if (-not (Test-Path $python)) { $python = 'python' }

$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root '.env.example') $envFile
    Write-Host ".env creato da .env.example: va compilato prima del canary." -ForegroundColor Yellow
}

Write-Host "`n== Ambiente ==" -ForegroundColor Cyan
& $python -m src.cli validate
if ($LASTEXITCODE -ne 0) { Write-Host "Ambiente incompleto." -ForegroundColor Red; exit 1 }

Write-Host "`n== Database e corpus ==" -ForegroundColor Cyan
& $python -m src.cli init-db
if ($LASTEXITCODE -ne 0) { exit 1 }
& $python -m src.cli import-content
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n== Cancelli ==" -ForegroundColor Cyan
$gates = @(
    'validate-datasets --skip-quality',
    'corpus-final-gate',
    'editorial-stats --strict',
    'security-check'
)
if (-not $SkipCorpus) {
    $gates += 'production-readiness --from 2026-08-07 --days 1000 --all-pages'
}
foreach ($gate in $gates) {
    $gateArgs = $gate.Split(' ')
    & $python -m src.cli @gateArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FALLITO  $gate" -ForegroundColor Red
        Write-Host "Rilancialo senza redirezione per vedere il dettaglio." -ForegroundColor DarkGray
        exit 1
    }
    Write-Host "  OK       $gate" -ForegroundColor Green
}

if (-not $SkipModel) {
    Write-Host "`n== Modello locale ==" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot 'install_local_model.ps1')
}

Write-Host "`n== Armamento (tutte le pagine partono disarmate) ==" -ForegroundColor Cyan
& $python -m src.cli arming-status

Write-Host "`nSetup completato. Passi successivi:" -ForegroundColor Green
Write-Host "  1. compila .env con le credenziali Meta"
Write-Host "  2. .\scripts\preflight.ps1"
Write-Host "  3. .\scripts\go_live_canary.ps1"
Write-Host "  4. .\scripts\arm_page.ps1 pensiero_essenziale_it"
