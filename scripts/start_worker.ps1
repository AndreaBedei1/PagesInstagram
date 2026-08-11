<#
.SYNOPSIS  Start the worker in the foreground (one tick or continuous).
#>
[CmdletBinding()] param([switch]$Once, [double]$IntervalSeconds = 60)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
& $python -m src.cli arming-status
if ($Once) { & $python -m src.cli worker --once }
else { & $python -m src.cli worker --interval $IntervalSeconds }
