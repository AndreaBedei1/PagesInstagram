<#
.SYNOPSIS Remove the worker Scheduled Task.
#>
[CmdletBinding()]
param([string]$TaskName = "InstagramContentEngineWorker")
$ErrorActionPreference = "Stop"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Task '$TaskName' rimosso." -ForegroundColor Green
} else {
  Write-Host "Task '$TaskName' non trovato."
}
