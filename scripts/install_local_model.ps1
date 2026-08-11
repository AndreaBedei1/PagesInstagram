<#
.SYNOPSIS
    Installa il checkpoint locale SDXL Base 1.0 per ComfyUI (nessun token, nessun
    modello committato nel repository).

.DESCRIPTION
    Fonte ufficiale verificata il 2026-08-04:
      repo      : https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
      file      : sd_xl_base_1.0.safetensors      (~6.94 GB)
      licenza   : CreativeML Open RAIL++-M  (tag Hugging Face: "openrail++")
      gated     : false  -> download diretto, nessuna accettazione interattiva,
                  nessun token Hugging Face richiesto.

    Lo script:
      1. verifica se il checkpoint esiste già (nessun download duplicato);
      2. lo scarica SOLO dalla fonte ufficiale sopra;
      3. calcola e registra lo SHA-256 in <models>/sd_xl_base_1.0.safetensors.sha256;
      4. mostra dove il file deve trovarsi per ComfyUI;
      5. esegue una generazione di prova via API ComfyUI (opzionale).

.PARAMETER ComfyUIRoot
    Cartella radice di ComfyUI (quella che contiene "models\checkpoints").
    Default: variabile d'ambiente ICE_COMFYUI_ROOT, altrimenti F:\AI\ComfyUI.

.PARAMETER SkipTestGeneration
    Non eseguire la generazione di prova.

.EXAMPLE
    scripts\install_local_model.ps1 -ComfyUIRoot "F:\AI\ComfyUI"
#>
[CmdletBinding()]
param(
    [string] $ComfyUIRoot = $(if ($env:ICE_COMFYUI_ROOT) { $env:ICE_COMFYUI_ROOT } else { "F:\AI\ComfyUI" }),
    [string] $ComfyUIUrl  = $(if ($env:ICE_COMFYUI_URL) { $env:ICE_COMFYUI_URL } else { "http://127.0.0.1:8188" }),
    [switch] $SkipTestGeneration
)

$ErrorActionPreference = "Stop"

# --- Fonte ufficiale (NON modificare senza riverificare la pagina del modello) --
$FileName    = "sd_xl_base_1.0.safetensors"
$RepoPage    = "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0"
$DownloadUrl = "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors?download=true"
$LicenseName = "CreativeML Open RAIL++-M"
$LicenseUrl  = "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md"

Write-Host "=== Installazione modello locale (SDXL Base 1.0) ===" -ForegroundColor Cyan
Write-Host "Repository ufficiale : $RepoPage"
Write-Host "Licenza              : $LicenseName ($LicenseUrl)"
Write-Host "File                 : $FileName"
Write-Host ""

$CheckpointDir = Join-Path $ComfyUIRoot "models\checkpoints"
if (-not (Test-Path $CheckpointDir)) {
    Write-Host "Cartella checkpoint non trovata: $CheckpointDir" -ForegroundColor Yellow
    Write-Host "Verrà creata. Se il percorso di ComfyUI è diverso usa -ComfyUIRoot." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $CheckpointDir | Out-Null
}
$Target    = Join-Path $CheckpointDir $FileName
$HashFile  = "$Target.sha256"

# --- 1) esiste già? -----------------------------------------------------------
if (Test-Path $Target) {
    $sizeGb = [math]::Round((Get-Item $Target).Length / 1GB, 2)
    Write-Host "Checkpoint già presente ($sizeGb GB): $Target" -ForegroundColor Green
    Write-Host "Nessun download necessario." -ForegroundColor Green
} else {
    Write-Host "Download da fonte ufficiale (~6.9 GB). Può richiedere parecchi minuti..." -ForegroundColor Cyan
    $tmp = "$Target.part"
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
    try {
        # curl.exe è incluso in Windows 10/11 e gestisce bene i file molto grandi.
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
            & curl.exe -L --fail --progress-bar -o $tmp $DownloadUrl
            if ($LASTEXITCODE -ne 0) { throw "curl ha restituito exit code $LASTEXITCODE" }
        } else {
            $ProgressPreference = "SilentlyContinue"
            Invoke-WebRequest -Uri $DownloadUrl -OutFile $tmp -UseBasicParsing
        }
        Move-Item $tmp $Target -Force
    } catch {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
        Write-Host ""
        Write-Host "Download fallito: $_" -ForegroundColor Red
        Write-Host "Scarica manualmente il file dalla pagina ufficiale:" -ForegroundColor Yellow
        Write-Host "  $RepoPage  ->  Files and versions  ->  $FileName" -ForegroundColor Yellow
        Write-Host "e copialo in: $CheckpointDir" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Download completato." -ForegroundColor Green
}

# --- 2) checksum --------------------------------------------------------------
if (Test-Path $HashFile) {
    Write-Host "Checksum già registrato: $HashFile" -ForegroundColor Green
    Get-Content $HashFile | Write-Host
} else {
    Write-Host "Calcolo SHA-256 (alcuni minuti su 6.9 GB)..." -ForegroundColor Cyan
    $hash = (Get-FileHash -Algorithm SHA256 -Path $Target).Hash.ToLower()
    "$hash  $FileName" | Set-Content -Path $HashFile -Encoding utf8
    Write-Host "SHA-256: $hash" -ForegroundColor Green
    Write-Host "Registrato in: $HashFile"
}

# --- 3) istruzioni di configurazione -----------------------------------------
Write-Host ""
Write-Host "Il modello deve trovarsi in:" -ForegroundColor Cyan
Write-Host "  $Target"
Write-Host ""
Write-Host "Configura l'engine (opzionale: i default coincidono già):" -ForegroundColor Cyan
Write-Host "  ICE_COMFYUI_MODEL_FAMILY=sdxl"
Write-Host "  ICE_COMFYUI_CHECKPOINT=$FileName"
Write-Host "  ICE_COMFYUI_WORKFLOW=sdxl_background.json"
Write-Host ""
Write-Host "NOTA: i file .safetensors NON vanno mai committati (sono in .gitignore)." -ForegroundColor Yellow

# --- 4) generazione di prova --------------------------------------------------
if ($SkipTestGeneration) {
    Write-Host "Generazione di prova saltata (-SkipTestGeneration)." -ForegroundColor Yellow
    exit 0
}

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Write-Host ""
Write-Host "Generazione di prova via ComfyUI ($ComfyUIUrl)..." -ForegroundColor Cyan
$env:ICE_COMFYUI_URL = $ComfyUIUrl
& $python -m src.cli comfyui test-generation --page pensiero_essenziale_it
exit $LASTEXITCODE
