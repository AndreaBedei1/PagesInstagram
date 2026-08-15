<#
.SYNOPSIS  Undo a go-live: stop, disarm, and return to dry-run.
.DESCRIPTION
    Does not delete anything already published — the API cannot — but guarantees
    nothing further goes out: the worker stops, every page is disarmed, and the
    engine returns to dry_run. Prints what was published so you can remove it
    from the app if you need to.

    -EnvFile points the ICE_MODE rewrite somewhere other than the operator's
    real .env. It exists because the tests run this script for real, with a
    stubbed Python — and the rewrite below is plain PowerShell, which no stub
    intercepts. Every full test run therefore put a production machine back into
    dry_run, silently, and the next worker restart published nothing while
    everything else still looked healthy. preflight.ps1 already had this seam;
    this script needed it just as much.
#>
[CmdletBinding()] param([switch]$KeepMode, [string]$PythonPath, [string]$EnvFile)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($PythonPath) {
    $python = $PythonPath
} else {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }
}

& (Join-Path $PSScriptRoot 'stop_worker.ps1') -Disarm -PythonPath $python

if (-not $KeepMode) {
    if ($EnvFile) { $envFile = $EnvFile } else { $envFile = Join-Path $root '.env' }
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
