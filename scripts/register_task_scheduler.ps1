<#
.SYNOPSIS
  Register a Windows Scheduled Task that starts the worker at logon and keeps it
  running (auto-restart on failure, runs when the PC becomes available after
  being off). Only starts the worker — all scheduling logic lives in the engine.
.NOTES
  Default trigger = AtLogOn (no stored password needed). To run at BOOT without
  a login, re-create the principal with -LogonType Password and provide your
  credentials (see README "Scheduler").
#>
[CmdletBinding()]
param(
  [string]$TaskName = "InstagramContentEngineWorker",
  [double]$Interval = 60
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$startScript = "$Root\scripts\start_worker.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`" -Interval $Interval" `
  -WorkingDirectory $Root

# Due trigger: all'accesso e ogni mattina. Con -StartWhenAvailable il task parte
# anche se il PC era spento all'orario previsto; il worker applica poi la
# missed_job_policy di ciascuna pagina (publish_within_window, 240 minuti).
$trigger = @(
  (New-ScheduledTaskTrigger -AtLogOn),
  (New-ScheduledTaskTrigger -Daily -At 07:00)
)

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 2) `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
  -MultipleInstances IgnoreNew `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Write-Host "Aggiorno il task esistente '$TaskName'..."
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal `
  -Description "Instagram Content Engine worker (plan -> generate -> publish)." | Out-Null

Write-Host "Task '$TaskName' registrato (avvio al login, riavvio automatico)." -ForegroundColor Green
Write-Host "Avvio immediato:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Rimozione:        scripts\unregister_task_scheduler.ps1"
