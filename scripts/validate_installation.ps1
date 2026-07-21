<#
.SYNOPSIS Validate the installation: environment check + tests + dry-run pipeline.
#>
[CmdletBinding()]
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw ".venv non trovato: esegui scripts\install_windows.ps1" }

Write-Host "== 1/3 Environment ==" -ForegroundColor Cyan
& $Py -m src.cli validate

if (-not $SkipTests) {
  Write-Host "== 2/3 Test suite ==" -ForegroundColor Cyan
  & $Py -m pytest -q
  if ($LASTEXITCODE -ne 0) { throw "Test falliti" }
}

Write-Host "== 3/3 Dry-run pipeline ==" -ForegroundColor Cyan
$env:ICE_MODE = "dry_run"
& $Py -m src.cli init-db
& $Py -m src.cli import-content
& $Py -m src.cli music generate --per-mood 1
& $Py -m src.cli music sync
& $Py -m src.cli generate --page motivational_it --count 1 --no-comfyui
& $Py -m src.cli preview
Write-Host "`nValidazione completata con successo." -ForegroundColor Green
