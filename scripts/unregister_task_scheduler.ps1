<#
.SYNOPSIS Remove whatever makes the worker start by itself.
.DESCRIPTION
    There are two mechanisms — the scheduled task and, where policy forbids it,
    a launcher in the per-user Startup folder — so removal has to know about
    both. Leaving one behind would mean a machine that keeps publishing after
    somebody believed they had stopped it.

    This does not stop a worker that is running now: use stop_worker.ps1 for
    that, or rollback.ps1 to stop and disarm.
#>
[CmdletBinding()]
param([string]$TaskName = "InstagramContentEngineWorker")
$ErrorActionPreference = "Continue"
$removed = 0

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Task '$TaskName' rimosso." -ForegroundColor Green
  $removed++
} else {
  Write-Host "Task '$TaskName' non trovato."
}

$vbs = Join-Path ([Environment]::GetFolderPath('Startup')) "$TaskName.vbs"
if (Test-Path $vbs) {
  Remove-Item $vbs -Force
  Write-Host "Avvio automatico rimosso: $vbs" -ForegroundColor Green
  $removed++
} else {
  Write-Host "Avvio automatico non presente."
}

if ($removed -eq 0) {
  Write-Host "Niente da rimuovere: il worker non riparte da solo." -ForegroundColor Yellow
} else {
  Write-Host "`nIl worker non ripartira' al prossimo accesso. Se ne sta girando" -ForegroundColor Yellow
  Write-Host "uno adesso, fermalo con scripts\stop_worker.ps1." -ForegroundColor Yellow
}
