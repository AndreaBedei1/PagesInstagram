<#
.SYNOPSIS Stop the running worker via its PID lock file (graceful).
#>
$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$lock = "$Root\logs\worker.lock"
if (-not (Test-Path $lock)) { Write-Host "Nessun lock: il worker non risulta in esecuzione."; return }
$pidText = (Get-Content $lock -Raw).Trim()
if ($pidText -match '^\d+$') {
  $procId = [int]$pidText
  $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
  if ($p) {
    Write-Host "Arresto worker PID $procId ..."
    Stop-Process -Id $procId -Force
    Write-Host "Worker arrestato." -ForegroundColor Green
  } else {
    Write-Host "Il PID $procId non è attivo (lock obsoleto)."
  }
} else {
  Write-Host "Lock non valido."
}
