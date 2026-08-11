<#
.SYNOPSIS
    One page, one upload, one confirmed publication. Nothing else.

.DESCRIPTION
    Three levels, and the difference between them is stated rather than implied:

      -WhatIf      zero Meta calls. Chooses the job, probes the file against the
                   published Reel specifications, shows the caption and the
                   target account, prints the steps it would take, and stops.
                   No container, no upload, no publication.

      -UploadOnly  a real container and a real resumable upload, and no
                   media_publish. Ends with the container FINISHED on Meta and
                   recorded against the job. Nothing appears on the account.

      (default)    reuses the container -UploadOnly left — or creates it if you
                   skipped that step — shows you exactly what will go out, and
                   publishes once, after you type PUBBLICA.

    The old version had -WhatIf uploading a real file and stopping one call
    short of publishing, which is a surprising thing for a parameter with that
    name to do. It also called the ordinary publisher at the end, which created
    a *second* container and re-uploaded the same Reel — and could not have
    worked anyway, because the ordinary publisher refuses an unarmed page and
    the page is only armed after this script succeeds.

    The page stays DISARMED afterwards. Arming is a separate decision, taken
    once you have looked at the post.

.EXAMPLE
    .\scripts\go_live_canary.ps1 -WhatIf
    .\scripts\go_live_canary.ps1 -UploadOnly
    .\scripts\go_live_canary.ps1
#>
[CmdletBinding()]
param(
    [string]$Page = 'pensiero_essenziale_it',
    [switch]$WhatIf,
    [switch]$UploadOnly,
    [int]$Job = 0,
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

function Step { param([string]$Text) Write-Host "`n== $Text ==" -ForegroundColor Cyan }
function Fail { param([string]$Text) Write-Host $Text -ForegroundColor Red; exit 1 }

if ($WhatIf -and $UploadOnly) {
    Fail "-WhatIf e -UploadOnly si escludono: il primo non tocca la rete, il secondo carica davvero."
}

# ---------------------------------------------------------------------------
# Level 1 — nothing leaves this machine.
# ---------------------------------------------------------------------------
if ($WhatIf) {
    Step "1/2 Controlli locali (nessuna chiamata a Meta)"
    & (Join-Path $PSScriptRoot 'preflight.ps1') -SkipCorpus -NoNetwork -Page $Page -PythonPath $python
    if ($LASTEXITCODE -ne 0) { Fail "Preflight non superato: risolvi prima di procedere." }

    Step "2/2 Simulazione del canary"
    $planArgs = @('-m', 'src.cli', 'instagram', 'canary-plan', '--page', $Page)
    if ($Job -gt 0) { $planArgs += @('--job', "$Job") }
    & $python @planArgs
    if ($LASTEXITCODE -ne 0) { Fail "La simulazione ha trovato un problema: risolvilo prima del canary reale." }

    Write-Host @"

-WhatIf completato.

  0 chiamate Meta
  0 container
  0 upload
  0 pubblicazioni

Passo successivo, quando vuoi caricare davvero senza pubblicare:

    .\scripts\go_live_canary.ps1 -UploadOnly
"@ -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# Levels 2 and 3 both need credentials and a green account.
# ---------------------------------------------------------------------------
Step "1. Preflight"
& (Join-Path $PSScriptRoot 'preflight.ps1') -SkipCorpus -Page $Page -PythonPath $python
if ($LASTEXITCODE -ne 0) { Fail "Preflight non superato: risolvi prima di procedere." }

Step "2. Che cosa verrebbe pubblicato"
$planArgs = @('-m', 'src.cli', 'instagram', 'canary-plan', '--page', $Page)
if ($Job -gt 0) { $planArgs += @('--job', "$Job") }
$planJson = & $python @planArgs --json
if ($LASTEXITCODE -ne 0 -or -not $planJson) {
    Fail "Nessun job futuro pronto per $Page. Prepara il buffer dalla data odierna: python -m src.cli prepare-buffer --from (Get-Date -Format 'yyyy-MM-dd') --days 30 --all-pages --require-comfyui"
}
$plan = $planJson | ConvertFrom-Json
$jobId = $plan.job_id
Write-Host "  job         : $jobId"
Write-Host "  programmato : $($plan.scheduled_at)"
Write-Host "  file        : $($plan.output_path)"
Write-Host "  specifiche  : $(if ($plan.audit_ok) { 'conformi' } else { 'NON conformi' })"
Write-Host "  container   : $(if ($plan.container_id) { $plan.container_id } else { 'ancora nessuno' })"

# ICE_MODE stays dry_run in .env. It is raised for this process only, and put
# back in the finally below, so a crash cannot leave the machine armed to talk
# to Meta.
$previousMode = $env:ICE_MODE
$env:ICE_MODE = 'test'
try {
    if ($plan.already_uploaded) {
        Step "3. Container già caricato: lo riuso"
        Write-Host "  $($plan.container_id) — non ne creo un secondo e non ricarico il file." -ForegroundColor DarkGray
    } else {
        Step "3. Upload reale, SENZA pubblicazione"
        $uploadArgs = @('-m', 'src.cli', 'instagram', 'canary-upload', '--page', $Page,
                        '--job', "$jobId")
        & $python @uploadArgs
        if ($LASTEXITCODE -ne 0) { Fail "Upload non riuscito. Nulla è stato pubblicato." }
    }

    # -----------------------------------------------------------------------
    # Level 2 stops here.
    # -----------------------------------------------------------------------
    if ($UploadOnly) {
        Write-Host @"

UPLOAD META RIUSCITO
MEDIA NON PUBBLICATO

Il container resta pronto sul job $jobId. Quando vuoi pubblicarlo:

    .\scripts\go_live_canary.ps1
"@ -ForegroundColor Green
        exit 0
    }

    # -----------------------------------------------------------------------
    # Level 3 — one publication, after the word.
    # -----------------------------------------------------------------------
    Step "4. Conferma esplicita"
    Write-Host "Sto per pubblicare UN post reale su $Page." -ForegroundColor Yellow
    Write-Host "Non è annullabile dall'API: potrai solo eliminarlo dall'app."
    Write-Host "Il container è già caricato: questa conferma esegue una sola media_publish."
    $answer = Read-Host "Scrivi PUBBLICA per procedere, qualsiasi altra cosa per annullare"
    if ($answer -cne 'PUBBLICA') {
        Write-Host "Annullato. Nessuna pubblicazione; il container resta pronto." -ForegroundColor Green
        exit 0
    }

    Step "5. Pubblicazione singola"
    & $python -m src.cli instagram publish-canary --page $Page --job $jobId --confirm PUBBLICA
    if ($LASTEXITCODE -ne 0) { Fail "Pubblicazione non riuscita o rifiutata. Controlla il messaggio qui sopra." }
}
finally {
    if ($previousMode) { $env:ICE_MODE = $previousMode } else { Remove-Item Env:ICE_MODE -ErrorAction SilentlyContinue }
}

Step "6. Verifica"
& $python -m src.cli instagram canary-status --page $Page
& $python -m src.cli arming-status
Write-Host @"

Canary completato: una sola pubblicazione, un solo media.

La pagina è ancora DISARMATA: il worker non pubblicherà nulla finché non lo dici
esplicitamente. Controlla il post sull'app, poi:

    .\scripts\arm_page.ps1 $Page

e dopo una settimana pulita:

    .\scripts\arm_all_pages.ps1
"@ -ForegroundColor Green
