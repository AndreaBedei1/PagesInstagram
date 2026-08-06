<#
.SYNOPSIS
    Everything that must be true before the first real Meta call.

.DESCRIPTION
    Without credentials it lists only the variables you still have to fill in
    and exits 2 — that is the expected outcome on a fresh machine, not a
    failure. With credentials present it checks the rest: accounts, tokens,
    expiry, Graph API version, ComfyUI, FFmpeg, disk, database, buffer, corpus
    and secrets.

    Never prints a token, never publishes anything.

.EXAMPLE
    .\scripts\preflight.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipCorpus,
    [int]$MinFreeGb = 5
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

$failures = @()
$warnings = @()

function Test-Step {
    param([string]$Name, [scriptblock]$Body, [switch]$Warn)
    Write-Host -NoNewline ("  {0,-42}" -f $Name)
    try {
        $detail = & $Body
        Write-Host "OK" -ForegroundColor Green -NoNewline
        if ($detail) { Write-Host "  $detail" -ForegroundColor DarkGray } else { Write-Host "" }
    } catch {
        if ($Warn) {
            Write-Host "AVVISO" -ForegroundColor Yellow -NoNewline
            Write-Host "  $($_.Exception.Message)" -ForegroundColor DarkGray
            $script:warnings += "$Name : $($_.Exception.Message)"
        } else {
            Write-Host "FALLITO" -ForegroundColor Red -NoNewline
            Write-Host "  $($_.Exception.Message)" -ForegroundColor DarkGray
            $script:failures += "$Name : $($_.Exception.Message)"
        }
    }
}

# ---------------------------------------------------------------- credentials
Write-Host "`n== Credenziali ==" -ForegroundColor Cyan

$pages = @('PENSIERO_ESSENZIALE_IT', 'CURIOSITA_MONDO_IT', 'PAROLA_GIORNO_IT',
           'OGGI_NELLA_STORIA_IT', 'DOMANDA_GIORNO_IT')
$required = @('META_APP_ID', 'META_APP_SECRET', 'META_GRAPH_API_VERSION')
foreach ($p in $pages) {
    $required += "ICE_${p}_IG_USER_ID"
    $required += "ICE_${p}_ACCESS_TOKEN"
}

# .env is read by the engine, not by PowerShell; load it here for the check only.
$envFile = Join-Path $root '.env'
$present = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
            $value = $Matches[2].Trim()
            if ($value) { $present[$Matches[1]] = $true }
        }
    }
} else {
    Write-Host "  .env assente" -ForegroundColor Yellow
}
foreach ($name in $required) {
    # $env:$name is a parse error; the environment provider takes a computed
    # name only through Get-Item.
    $value = (Get-Item -Path "Env:$name" -ErrorAction SilentlyContinue).Value
    if ($value) { $present[$name] = $true }
}

$missing = $required | Where-Object { -not $present.ContainsKey($_) }
if ($missing.Count -gt 0) {
    Write-Host "`nVariabili ancora da compilare in .env:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  $_" }
    Write-Host "`nCopia .env.example in .env e compilale, poi rilancia questo script."
    Write-Host "Nessun'altra verifica è stata eseguita: senza credenziali non avrebbe senso."
    exit 2
}
Write-Host "  tutte le $($required.Count) variabili richieste sono presenti" -ForegroundColor Green

# ---------------------------------------------------------------- environment
Write-Host "`n== Ambiente ==" -ForegroundColor Cyan
Test-Step 'validate (ffmpeg, font, database, pagine)' {
    & $python -m src.cli validate | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "uscita $LASTEXITCODE" }
}
Test-Step 'versione Graph API coerente ovunque' {
    & $python -m pytest -q tests/unit/test_meta_api_version.py | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "codice e documentazione divergono" }
}
Test-Step 'spazio su disco' {
    $free = (Get-PSDrive -Name (Split-Path -Qualifier $root).TrimEnd(':')).Free / 1GB
    if ($free -lt $MinFreeGb) { throw ("{0:N1} GB liberi, minimo {1}" -f $free, $MinFreeGb) }
    "{0:N1} GB liberi" -f $free
}
Test-Step 'ComfyUI raggiungibile' {
    & $python -m src.cli comfyui status | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "non raggiungibile: avvialo prima di generare media" }
} -Warn

# ---------------------------------------------------------------- corpus
if (-not $SkipCorpus) {
    Write-Host "`n== Corpus ==" -ForegroundColor Cyan
    Test-Step 'cancello finale del corpus' {
        & $python -m src.cli corpus-final-gate | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "il corpus non è pronto per la produzione" }
    }
    Test-Step 'copertura del ciclo completo' {
        & $python -m src.cli production-readiness --from (Get-Date -Format 'yyyy-MM-dd') --days 1000 --all-pages | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "un giorno del ciclo non è pubblicabile" }
    }
}

# ---------------------------------------------------------------- security
Write-Host "`n== Sicurezza ==" -ForegroundColor Cyan
Test-Step 'nessun segreto nei file tracciati' {
    & $python -m src.cli security-check | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "lo scanner ha trovato qualcosa" }
}

# ---------------------------------------------------------------- accounts
Write-Host "`n== Account Meta ==" -ForegroundColor Cyan
Test-Step 'health check delle cinque pagine' {
    & $python -m src.cli instagram health-check --all
    if ($LASTEXITCODE -eq 1) { throw "almeno una pagina non è utilizzabile" }
    if ($LASTEXITCODE -eq 2) { throw "solo avvisi (scadenza token vicina?)" }
} -Warn

Write-Host "`n== Armamento ==" -ForegroundColor Cyan
& $python -m src.cli arming-status

# ---------------------------------------------------------------- verdict
Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "PREFLIGHT FALLITO — $($failures.Count) controlli da sistemare:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  $_" }
    exit 1
}
if ($warnings.Count -gt 0) {
    Write-Host "PREFLIGHT CON AVVISI — $($warnings.Count):" -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host "  $_" }
}
Write-Host "PREFLIGHT SUPERATO." -ForegroundColor Green
Write-Host "Passo successivo: .\scripts\go_live_canary.ps1"
exit 0
