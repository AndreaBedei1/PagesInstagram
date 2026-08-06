<#
.SYNOPSIS  Arm one page for real publication, after its canary.
.DESCRIPTION
    Arming is the second switch: ICE_MODE=production alone never publishes.
    Every page starts disarmed and nothing arms one automatically.
    This does not change ICE_MODE and does not publish anything.
.EXAMPLE  .\scripts\arm_page.ps1 pensiero_essenziale_it
#>
[CmdletBinding()]
param([Parameter(Mandatory)][string]$Page, [switch]$Disarm)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
if ($Disarm) { & $python -m src.cli arm-page $Page --disarm }
else { & $python -m src.cli arm-page $Page }
exit $LASTEXITCODE
