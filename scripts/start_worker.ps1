<#
.SYNOPSIS Start the persistent worker (foreground). Used by Task Scheduler.
#>
[CmdletBinding()]
param([double]$Interval = 60)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw ".venv non trovato: esegui scripts\install_windows.ps1" }

New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = "$Root\logs\worker_$stamp.out.log"
Write-Host "Avvio worker (interval=$Interval s). Log: $log"
& $Py -m src.cli worker --interval $Interval *>> $log
