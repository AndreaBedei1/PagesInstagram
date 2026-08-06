<#
.SYNOPSIS  Undo a go-live: stop, disarm, and return to dry-run.
.DESCRIPTION
    Does not delete anything already published — the API cannot — but guarantees
    nothing further goes out: the worker stops, every page is disarmed, and the
    engine returns to dry_run. Prints what was published so you can remove it
    from the app if you need to.
#>
[CmdletBinding()] param([switch]$KeepMode)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

& (Join-Path $PSScriptRoot 'stop_worker.ps1') -Disarm

if (-not $KeepMode) {
    $envFile = Join-Path $root '.env'
    if (Test-Path $envFile) {
        $content = Get-Content $envFile
        $updated = $content -replace '^\s*ICE_MODE\s*=.*$', 'ICE_MODE=dry_run'
        if ($updated -join "`n" -ne ($content -join "`n")) {
            Set-Content -Path $envFile -Value $updated -Encoding utf8
            Write-Host "ICE_MODE riportato a dry_run in .env" -ForegroundColor Green
        }
    }
}
Write-Host "`nGià pubblicato (da rimuovere a mano dall'app, se necessario):" -ForegroundColor Yellow
& $python -m src.cli status
Write-Host "`nRollback completato: il sistema non pubblicherà altro." -ForegroundColor Green
