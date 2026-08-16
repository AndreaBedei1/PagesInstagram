<#
.SYNOPSIS
    Make the worker survive reboots, by whichever mechanism this machine allows.

.DESCRIPTION
    The scheduled task is the better one where it can be created: it restarts
    the worker on failure and starts it even if the PC was off at the trigger
    time. It is not always available — on a machine with a policy against it
    both Register-ScheduledTask and schtasks.exe answer "Accesso negato"
    (HRESULT 0x80070005), and no amount of retrying changes that.

    So this tries the task, and falls back to the per-user Startup folder, which
    needs no privileges. It says which of the two it ended up using, because
    "installed" without saying how is exactly the kind of sentence that reads
    fine and leaves a machine publishing nothing.

    The previous version printed its success message before the registration
    that failed, so a blocked machine looked like an installed one.

.EXAMPLE
    .\scripts\install_worker.ps1
    .\scripts\install_worker.ps1 -Startup     # salta il task, usa l'avvio utente
#>
[CmdletBinding()]
param([string]$TaskName = 'InstagramContentEngineWorker',
      [switch]$Startup)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot

function Use-Startup {
    & (Join-Path $PSScriptRoot 'install_worker_startup.ps1') -Name $TaskName
    return $LASTEXITCODE
}

if ($Startup) {
    exit (Use-Startup)
}

$taskError = $null
try {
    & (Join-Path $PSScriptRoot 'register_task_scheduler.ps1') -TaskName $TaskName
} catch {
    $taskError = $_
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Meccanismo attivo: attivita' pianificata '$TaskName'." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "Attivita' pianificata non creata su questa macchina." -ForegroundColor Yellow
if ($taskError) { Write-Host "  $($taskError.Exception.Message)" -ForegroundColor DarkGray }
Write-Host "Ripiego sull'avvio automatico per l'utente corrente, che non richiede privilegi."
Write-Host ""
exit (Use-Startup)
