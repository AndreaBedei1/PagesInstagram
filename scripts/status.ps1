<#
.SYNOPSIS  What the engine is doing right now.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

Write-Host "== Modalità e armamento ==" -ForegroundColor Cyan
& $python -m src.cli arming-status
Write-Host "`n== Contenuti e job ==" -ForegroundColor Cyan
& $python -m src.cli status
Write-Host "`n== Worker ==" -ForegroundColor Cyan
$task = Get-ScheduledTask -TaskName 'InstagramContentEngineWorker' -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName 'InstagramContentEngineWorker'
    Write-Host "  stato        : $($task.State)"
    Write-Host "  ultima esec. : $($info.LastRunTime)  esito $($info.LastTaskResult)"
    Write-Host "  prossima     : $($info.NextRunTime)"
} else {
    Write-Host "  attività pianificata non registrata (scripts\install_worker.ps1)"
}
$lock = Join-Path $root 'logs\worker.lock'
if (Test-Path $lock) { Write-Host "  lock         : presente ($(Get-Content $lock -Raw))" }
