<#
.SYNOPSIS
    One page, one upload, one confirmed publication. Nothing else.

.DESCRIPTION
    The controlled first contact with Meta. It uploads a real Reel for
    pensiero_essenziale_it and stops before publishing, shows you exactly what
    would go out, and publishes only after you type the confirmation word.

    Nothing here runs by itself: the confirmation is interactive on purpose, and
    -WhatIf walks the whole sequence without the final call.

    Never run during development. Requires credentials in .env.

.EXAMPLE
    .\scripts\go_live_canary.ps1 -WhatIf     # everything except media_publish
    .\scripts\go_live_canary.ps1
#>
[CmdletBinding()]
param(
    [string]$Page = 'pensiero_essenziale_it',
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

function Step { param([string]$Text) Write-Host "`n== $Text ==" -ForegroundColor Cyan }
function Fail { param([string]$Text) Write-Host $Text -ForegroundColor Red; exit 1 }

Step "1. Preflight"
& (Join-Path $PSScriptRoot 'preflight.ps1') -SkipCorpus
if ($LASTEXITCODE -ne 0) { Fail "Preflight non superato: risolvi prima di procedere." }

Step "2. Health check della sola pagina canary"
& $python -m src.cli instagram health-check --page $Page
if ($LASTEXITCODE -eq 1) { Fail "La pagina $Page non è utilizzabile." }

Step "3. Un Reel reale, generato senza pubblicare"
# The buffer already holds media; take the next one due for this page.
$jobJson = & $python -m src.cli next-job --page $Page --json
if ($LASTEXITCODE -ne 0 -or -not $jobJson) {
    Fail "Nessun job pronto per $Page. Esegui prima: python -m src.cli prepare-buffer --days 1 --all-pages"
}
$job = $jobJson | ConvertFrom-Json
Write-Host "  job        : $($job.id)"
Write-Host "  data        : $($job.scheduled_at)"
Write-Host "  file        : $($job.output_path)"
Write-Host "  contenuto   : $($job.text)"

Step "4. Controllo del file contro le specifiche Meta"
& $python -m src.cli media-audit --file $job.output_path
if ($LASTEXITCODE -ne 0) { Fail "Il file non rispetta le specifiche: non lo carico." }

Step "5. Upload reale, SENZA pubblicazione"
Write-Host "  ICE_MODE viene impostato a 'test' solo per questo passaggio." -ForegroundColor DarkGray
$previousMode = $env:ICE_MODE
$env:ICE_MODE = 'test'
try {
    & $python -m src.cli instagram upload-test --page $Page --file $job.output_path --no-publish
    if ($LASTEXITCODE -ne 0) { Fail "Upload non riuscito. Nulla è stato pubblicato." }

    Step "6. Riepilogo di ciò che verrebbe pubblicato"
    & $python -m src.cli instagram account-status --page $Page
    Write-Host "`n  Didascalia:" -ForegroundColor Yellow
    & $python -m src.cli caption --job $job.id
    Write-Host "`n  File: $($job.output_path)"

    if ($WhatIf) {
        Write-Host "`n-WhatIf: mi fermo qui. Nessuna pubblicazione." -ForegroundColor Green
        exit 0
    }

    Step "7. Conferma esplicita"
    Write-Host "Sto per pubblicare UN post reale su $Page." -ForegroundColor Yellow
    Write-Host "Non è annullabile dall'API: potrai solo eliminarlo dall'app."
    $answer = Read-Host "Scrivi PUBBLICA per procedere, qualsiasi altra cosa per annullare"
    if ($answer -cne 'PUBBLICA') {
        Write-Host "Annullato. Nessuna pubblicazione." -ForegroundColor Green
        exit 0
    }

    Step "8. Pubblicazione singola"
    & $python -m src.cli instagram publish-job --page $Page --job $job.id --confirm
    if ($LASTEXITCODE -ne 0) { Fail "Pubblicazione non riuscita." }
}
finally {
    if ($previousMode) { $env:ICE_MODE = $previousMode } else { Remove-Item Env:ICE_MODE -ErrorAction SilentlyContinue }
}

Step "9. Verifica"
& $python -m src.cli status
Write-Host @"

Canary completato.

La pagina non è ancora armata: il worker non pubblicherà nulla finché non lo dici
esplicitamente. Controlla il post sull'app, poi:

    .\scripts\arm_page.ps1 $Page

e dopo una settimana pulita:

    .\scripts\arm_all_pages.ps1
"@ -ForegroundColor Green
