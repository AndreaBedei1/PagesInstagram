<#
.SYNOPSIS  Stop publishing now.
.DESCRIPTION
    Stops the scheduled task and the running worker. Media already uploaded to
    Meta but not published stay as containers and expire on their own after 24
    hours; nothing is lost and nothing is published.
    Use -Disarm to also disarm every page, so a restart cannot resume publishing.
#>
[CmdletBinding()] param([switch]$Disarm, [string]$PythonPath)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($PythonPath) {
    $python = $PythonPath
} else {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }
}

Write-Host "Arresto dell'attività pianificata…" -ForegroundColor Cyan
Stop-ScheduledTask -TaskName 'InstagramContentEngineWorker' -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName 'InstagramContentEngineWorker' -ErrorAction SilentlyContinue | Out-Null

$lock = Join-Path $root 'logs\worker.lock'
if (Test-Path $lock) {
    $processId = (Get-Content $lock -Raw).Trim()
    if ($processId -match '^\d+$') {
        Write-Host "Arresto del worker (PID $processId)…" -ForegroundColor Cyan
        Stop-Process -Id ([int]$processId) -ErrorAction SilentlyContinue
    }
}
if ($Disarm) {
    Write-Host "Disarmo di tutte le pagine…" -ForegroundColor Cyan
    & $python -m src.cli arm-page all --disarm
}
Write-Host "Fermo. Nessuna pubblicazione in corso." -ForegroundColor Green
