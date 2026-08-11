<#
.SYNOPSIS
    Everything that must be true before the first real Meta call.

.DESCRIPTION
    Two profiles, because "may I publish one post by hand" and "may this
    machine publish unattended for the next month" are different questions:

      -Canary      the default. ComfyUI may be off if the buffer already
                   covers the days ahead: the canary publishes media that
                   exists, it does not generate any.
      -Production  what the scheduler needs. ComfyUI must be reachable and
                   configured, the production fallback background is
                   forbidden, and the buffer must actually cover the window.

    Credentials are never a warning. The health check's exit code decides:

      0  every account green            -> OK
      1  at least one failure           -> PREFLIGHT FALLITO
      2  warnings only                  -> PREFLIGHT FALLITO

    Two is a failure here on purpose. A token nobody can date, an account of
    the wrong type, a permission that may or may not be granted — none of those
    is a state to start five accounts from, and the whole point of a preflight
    is to be the place that says so.

    Never prints a token, never publishes anything.

.PARAMETER PythonPath
    Interpreter to use. Exists so tests/unit/test_powershell_scripts.py can
    drive this script with a stub and check what it decides, rather than
    grepping it for strings.

.EXAMPLE
    .\scripts\preflight.ps1
    .\scripts\preflight.ps1 -Production
#>
[CmdletBinding()]
param(
    [switch]$Canary,
    [switch]$Production,
    [switch]$SkipCorpus,
    [switch]$NoNetwork,
    [string]$Page,
    [string]$EnvFile,
    [int]$MinFreeGb = 5,
    [int]$BufferDays = 30,
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($PythonPath) {
    $python = $PythonPath
} else {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }
}
if ($Production) { $profileName = 'production' } else { $profileName = 'canary' }

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

Write-Host "== Profilo: $profileName ==" -ForegroundColor Cyan

# ---------------------------------------------------------------- credentials
Write-Host "`n== Credenziali ==" -ForegroundColor Cyan

$allPages = @('PENSIERO_ESSENZIALE_IT', 'CURIOSITA_MONDO_IT', 'PAROLA_GIORNO_IT',
              'OGGI_NELLA_STORIA_IT', 'DOMANDA_GIORNO_IT')
# -Page narrows the credential check to one account. The canary publishes one
# post to one page, and demanding all five accounts before it can run defeats
# the point of a canary: the other four are meant to stay unarmed, and their
# tokens are meant to be generated later, after this one has proved the flow.
# The scheduler is the case that genuinely needs all five, and that is what
# -Production without -Page checks.
if ($Page) {
    $pages = @($Page.ToUpper())
    if ($allPages -notcontains $pages[0]) {
        Write-Host "Pagina sconosciuta: $Page" -ForegroundColor Red
        Write-Host "Attese: $($allPages -join ', ')"
        exit 2
    }
    Write-Host "Credenziali richieste solo per: $($pages[0])" -ForegroundColor DarkGray
} else {
    $pages = $allPages
}
# What publishing actually needs, per the official Instagram Platform docs:
# an Instagram User access token and the account id. Nothing else.
$required = @('META_GRAPH_API_VERSION')
foreach ($p in $pages) {
    $required += "ICE_${p}_IG_USER_ID"
    $required += "ICE_${p}_ACCESS_TOKEN"
}
# Needed only to read a token's expiry and scopes (GET /debug_token, which
# lives on graph.facebook.com and wants an app access token), and to exchange a
# short-lived token for a long-lived one. Absent, publishing still works — but
# the health check cannot date the token, and that becomes a blocking warning.
$optional = @('META_APP_ID', 'META_APP_SECRET')

# .env is read by the engine, not by PowerShell; load it here for the check only.
# The tests point this at a temporary file: a suite whose result depends
# on whether the operator has configured this machine is not a suite.
if ($EnvFile) { $envFile = $EnvFile } else { $envFile = Join-Path $root '.env' }
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
foreach ($name in ($required + $optional)) {
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
Write-Host "  tutte le $($required.Count) variabili necessarie sono presenti" -ForegroundColor Green
$missingOptional = $optional | Where-Object { -not $present.ContainsKey($_) }
if ($missingOptional.Count -gt 0) {
    Write-Host "  facoltative non impostate: $($missingOptional -join ', ')" -ForegroundColor DarkGray
    Write-Host "  non servono per pubblicare; senza di esse la scadenza del token" -ForegroundColor DarkGray
    Write-Host "  non è leggibile e il health check resta in avviso." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- flavor
# Before anything reaches the network: is this (api_flavor, upload_method)
# pair one Meta implements? instagram_login + resumable is not, and finding
# that out from Meta halfway through a real upload cost a go-live.
Write-Host "`n== Configurazione di pubblicazione ==" -ForegroundColor Cyan
foreach ($p in $pages) {
    $pageId = $p.ToLower()
    Test-Step "flavor e metodo di upload ($pageId)" {
        & $python -m src.cli instagram check-config --page $pageId | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "combinazione api_flavor/upload_method non supportata: esegui 'python -m src.cli instagram check-config --page $pageId' per il dettaglio"
        }
    }
}

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
    # Split-Path -Qualifier wants a drive letter, and a path like
    # /home/runner/work has none. The engine only runs on Windows, but this
    # script is exercised by the test suite on the CI runners too, and a check
    # that cannot apply must say so rather than fail the preflight.
    $drive = $null
    try {
        $drive = Get-PSDrive -Name (Split-Path -Qualifier $root).TrimEnd(':') -ErrorAction Stop
    } catch {
        $drive = $null
    }
    if ($null -eq $drive -or $null -eq $drive.Free) {
        return 'non misurabile su questa piattaforma'
    }
    $free = $drive.Free / 1GB
    if ($free -lt $MinFreeGb) { throw ("{0:N1} GB liberi, minimo {1}" -f $free, $MinFreeGb) }
    "{0:N1} GB liberi" -f $free
}

# ComfyUI: a warning for the canary, a failure for the scheduler. The canary
# publishes a file that already exists; the scheduler has to keep producing
# them, and a page whose media generator is down goes quiet within days.
$comfyStep = {
    & $python -m src.cli comfyui status | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "non raggiungibile: senza ComfyUI non si generano nuovi media"
    }
}
if ($Production) {
    Test-Step 'ComfyUI raggiungibile e configurato' $comfyStep
} else {
    Test-Step 'ComfyUI raggiungibile' $comfyStep -Warn
}

# ---------------------------------------------------------------- buffer
Write-Host "`n== Buffer ==" -ForegroundColor Cyan
$bufferStep = {
    & $python -m src.cli buffer-status --days $BufferDays | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "meno di $BufferDays giorni coperti dalla data odierna"
    }
}
if ($Production) {
    Test-Step "copertura di $BufferDays giorni" $bufferStep
} else {
    # For the canary one publishable future job is enough, and that is what
    # canary-plan checks; the full window is the scheduler's problem.
    Test-Step "copertura di $BufferDays giorni" $bufferStep -Warn
}

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
# The only section that talks to Meta. -NoNetwork skips it so that
# go_live_canary.ps1 -WhatIf can promise zero Meta calls and mean it.
Write-Host "`n== Account Meta ==" -ForegroundColor Cyan
if ($NoNetwork) {
    Write-Host "  saltato (-NoNetwork): nessuna chiamata a Meta" -ForegroundColor DarkGray
} else {
$healthLabel = if ($Page) { "health check di $Page" } else { 'health check delle cinque pagine' }
# [string[]] is load-bearing: PowerShell unwraps a one-element array to a
# scalar on assignment, and splatting a string spreads its characters —
# `health-check - - a l l`. The behaviour tests caught it; the type keeps
# it caught.
[string[]]$healthArgs = if ($Page) { @('--page', $Page) } else { @('--all') }
Test-Step $healthLabel {
    # Out-Host, not capture: Test-Step assigns a block's output to
    # $detail and prints it on one line, which flattens the health
    # table into an unreadable smear. This is the one step whose output
    # the operator has to read.
    Write-Host ''
    & $python -m src.cli instagram health-check @healthArgs | Out-Host
    $code = $LASTEXITCODE
    if ($code -eq 1) {
        throw "almeno una pagina non è utilizzabile (token, permessi o account)"
    }
    if ($code -eq 2) {
        throw ("solo avvisi, e per il primo go-live un avviso sulle credenziali " +
               "è bloccante: scadenza vicina o non leggibile, account non " +
               "business, permessi non verificabili")
    }
    if ($code -ne 0) { throw "uscita inattesa $code" }
}
}

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
if ($Production) {
    Write-Host "Lo scheduler può essere avviato: .\scripts\install_worker.ps1"
} else {
    Write-Host "Passo successivo: .\scripts\go_live_canary.ps1 -WhatIf"
}
exit 0
