<#
.SYNOPSIS  What the engine is doing right now.
#>
[CmdletBinding()] param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($PythonPath) {
    $python = $PythonPath
} else {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }
}

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
    # Not necessarily a problem: where policy forbids scheduled tasks the engine
    # starts from the per-user Startup folder instead. Reporting only the task
    # would call a working machine unconfigured.
    $vbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'InstagramContentEngineWorker.vbs'
    if (Test-Path $vbs) {
        Write-Host "  avvio        : automatico all'accesso (Esecuzione automatica)"
        Write-Host "                 $vbs"
    } else {
        Write-Host "  avvio        : NON automatico — non riparte dopo un riavvio"
        Write-Host "                 (scripts\install_worker.ps1)"
    }
}
# The running worker holds an exclusive lock on this file, so Get-Content
# throws exactly when the answer is most interesting. A lock that cannot be
# read is not an error: it is the strongest evidence there is that a worker is
# alive right now. Reading it successfully means the opposite — nobody is
# holding it, and the PID inside is left over from a previous run.
$lock = Join-Path $root 'logs\worker.lock'
if (Test-Path $lock) {
    $pidText = $null
    try {
        $pidText = (Get-Content $lock -Raw -ErrorAction Stop).Trim()
    } catch {
        Write-Host "  lock         : trattenuto da un worker in esecuzione"
    }
    if ($null -ne $pidText) {
        $owner = $null
        if ($pidText -match '^\d+$') {
            $owner = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        }
        if ($owner) {
            Write-Host "  lock         : presente (PID $pidText, processo vivo)"
        } else {
            Write-Host "  lock         : file residuo (PID $pidText non esiste piu')"
        }
    }
}
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*src.cli*worker*' }
if ($running) {
    Write-Host "  processo     : in esecuzione (PID $(($running | ForEach-Object { $_.ProcessId }) -join ', '))"
} else {
    Write-Host "  processo     : nessun worker in esecuzione"
}
