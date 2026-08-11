<#
.SYNOPSIS
    Bootstrap completo delle cinque pagine evergreen su un'installazione pulita.

.DESCRIPTION
    Porta il progetto da "repository appena clonato" a "dry-run funzionante",
    senza mai pubblicare nulla e senza richiedere credenziali Meta.

    Esegue in ordine:
      1.  installa le dipendenze in .venv (uv, se disponibile; altrimenti venv+pip)
      2.  crea .env da .env.example, se manca
      3.  valida l'ambiente (ffmpeg, font, ComfyUI, dataset, pagine)
      4.  inizializza il database e registra le cinque pagine
      5.  valida i cinque dataset (exit code != 0 se anche un requisito manca)
      6.  importa i contenuti (dedup + qualità + controllo delle fonti)
      7.  esegue il controllo segreti
      8.  pianifica il buffer di job e prepara i media del buffer scorrevole
      9.  esegue un dry-run completo delle cinque pagine
     10.  genera le anteprime e il campione di revisione
     11.  (opzionale) registra il task di avvio automatico del worker

    Idempotente: può essere rieseguito in qualsiasi momento.

.PARAMETER Days
    Giorni da pianificare nel dry-run iniziale (default 7).

.PARAMETER RegisterTask
    Registra anche il task di Windows che avvia il worker al login.

.PARAMETER SkipPreviews
    Salta la generazione delle anteprime (più veloce).

.EXAMPLE
    scripts\bootstrap_five_pages.ps1
    scripts\bootstrap_five_pages.ps1 -Days 30 -RegisterTask
#>
[CmdletBinding()]
param(
    [int]    $Days = 7,
    [switch] $RegisterTask,
    [switch] $SkipPreviews
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONIOENCODING = "utf-8"

function Step([string] $Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Require([int] $Code, [string] $What) {
    if ($Code -ne 0) { throw "$What ha restituito exit code $Code" }
}

Write-Host "=== Bootstrap cinque pagine evergreen ===" -ForegroundColor Green
Write-Host "Repository: $Root"
Write-Host "Modalita'  : dry_run (nessuna pubblicazione reale)"

# --- 1) ambiente Python ------------------------------------------------------
Step "1/11  Ambiente Python e dipendenze"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv "$Root\.venv"
    } else {
        Write-Host "uv non trovato: uso il modulo venv di sistema." -ForegroundColor Yellow
        python -m venv "$Root\.venv"
    }
}
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python $Py -r "$Root\requirements.txt"
} else {
    & $Py -m pip install --upgrade pip
    & $Py -m pip install -r "$Root\requirements.txt"
}
Require $LASTEXITCODE "installazione dipendenze"

# --- 2) .env -----------------------------------------------------------------
Step "2/11  File .env locale"
if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    Write-Host ".env creato da .env.example. I token restano vuoti: e' corretto." -ForegroundColor Yellow
} else {
    Write-Host ".env gia' presente: non viene toccato."
}
$env:ICE_MODE = "dry_run"

# --- 3) validazione ambiente -------------------------------------------------
Step "3/11  Validazione ambiente"
& $Py -m src.cli validate
if ($LASTEXITCODE -ne 0) {
    Write-Host "Alcuni controlli non sono passati (ComfyUI assente e' accettabile in dry-run)." -ForegroundColor Yellow
}

# --- 4) database -------------------------------------------------------------
Step "4/11  Database e registrazione pagine"
& $Py -m src.cli init-db
Require $LASTEXITCODE "init-db"

# --- 5) validazione dataset --------------------------------------------------
Step "5/11  Validazione dei cinque dataset"
& $Py -m src.cli validate-datasets
Require $LASTEXITCODE "validate-datasets"

# --- 6) import contenuti -----------------------------------------------------
Step "6/11  Import dei contenuti (dedup, qualita', fonti)"
& $Py -m src.cli import-content
Require $LASTEXITCODE "import-content"

# --- 7) sicurezza ------------------------------------------------------------
Step "7/11  Controllo segreti"
& $Py -m src.cli security-check
Require $LASTEXITCODE "security-check"

# --- 8) buffer ---------------------------------------------------------------
Step "8/11  Pianificazione job e preparazione del buffer"
& $Py -m src.cli schedule --days $Days
Require $LASTEXITCODE "schedule"
& $Py -m src.cli worker --once --no-comfyui
Require $LASTEXITCODE "worker --once"

# --- 9) dry-run --------------------------------------------------------------
Step "9/11  Dry-run delle cinque pagine"
& $Py -m src.cli status

# --- 10) anteprime -----------------------------------------------------------
if ($SkipPreviews) {
    Step "10/11 Anteprime saltate (-SkipPreviews)"
} else {
    Step "10/11 Anteprime e campione di revisione"
    & $Py -m src.cli preview-pages --per-page 5
    & $Py -m src.cli sample-review --count 25
}

# --- 11) task scheduler ------------------------------------------------------
if ($RegisterTask) {
    Step "11/11 Registrazione del task di avvio automatico"
    & "$Root\scripts\register_task_scheduler.ps1"
} else {
    Step "11/11 Task scheduler non registrato (usa -RegisterTask)"
}

Write-Host ""
Write-Host "=== Bootstrap completato ===" -ForegroundColor Green
Write-Host "Report validazione : reports\datasets_validation.json"
Write-Host "Anteprime          : reports\previews\index.html"
Write-Host "Campione revisione : reports\sample_review.html"
Write-Host ""
Write-Host "Passi successivi:" -ForegroundColor Cyan
Write-Host "  1. installa il modello locale : scripts\install_local_model.ps1"
Write-Host "  2. inserisci i token in .env  : ICE_<PAGINA>_IG_USER_ID / _ACCESS_TOKEN"
Write-Host "  3. verifica i token           : $Py -m src.cli instagram health-check --all"
Write-Host "  4. passa alla produzione      : imposta ICE_MODE=production in .env"
