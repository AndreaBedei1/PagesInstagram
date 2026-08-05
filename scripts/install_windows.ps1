<#
.SYNOPSIS
  Fresh Windows install of the Instagram Content Engine (uv + .venv).
.DESCRIPTION
  Creates a clean virtual environment, installs dependencies, verifies the
  toolchain, initializes the database and imports the seed datasets + music.
  Re-runnable (idempotent).
#>
[CmdletBinding()]
param(
  [switch]$SkipDatasets,
  [switch]$SkipMusic
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "== Instagram Content Engine — install ==" -ForegroundColor Cyan
Write-Host "Repo: $Root"

# 1) uv present?
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "uv non trovato: installalo (https://docs.astral.sh/uv/) e riesegui." -ForegroundColor Yellow
  Write-Host "  Esempio: irm https://astral.sh/uv/install.ps1 | iex"
  throw "uv mancante"
}
uv --version

# 2) create venv (Python >= 3.11; uses the newest available)
if (-not (Test-Path "$Root\.venv")) {
  Write-Host "Creazione .venv..." -ForegroundColor Cyan
  uv venv "$Root\.venv"
}
$Py = "$Root\.venv\Scripts\python.exe"

# 3) install deps
Write-Host "Installazione dipendenze..." -ForegroundColor Cyan
uv pip install --python $Py -r "$Root\requirements.txt"

# 4) .env
if (-not (Test-Path "$Root\.env")) {
  Copy-Item "$Root\.env.example" "$Root\.env"
  Write-Host "Creato .env da .env.example (compila i token per la produzione)." -ForegroundColor Yellow
}

# 5) validate toolchain
Write-Host "Validazione ambiente..." -ForegroundColor Cyan
& $Py -m src.cli validate

# 6) DB + datasets + music
& $Py -m src.cli init-db
if (-not $SkipDatasets) {
  & $Py -m src.cli validate-datasets
  if ($LASTEXITCODE -ne 0) { throw "validate-datasets ha rilevato errori bloccanti" }
  & $Py -m src.cli import-content
}
if (-not $SkipMusic) {
  & $Py -m src.cli music generate --per-mood 1
  & $Py -m src.cli music sync
}

# 7) controllo segreti (nessun token deve essere tracciato da Git)
& $Py -m src.cli security-check
if ($LASTEXITCODE -ne 0) { throw "security-check ha rilevato un possibile segreto" }

Write-Host "`nInstallazione completata." -ForegroundColor Green
Write-Host "Bootstrap completo cinque pagine: scripts\bootstrap_five_pages.ps1"
Write-Host "Prova la pipeline (dry-run):      $Py -m src.cli generate --page pensiero_essenziale_it --count 1"
Write-Host "Modello locale SDXL:              scripts\install_local_model.ps1"
Write-Host "Registra l'avvio automatico:      scripts\register_task_scheduler.ps1"
