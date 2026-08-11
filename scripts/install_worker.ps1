<#
.SYNOPSIS  Register the worker as a scheduled task (logon + daily catch-up).
#>
[CmdletBinding()] param([string]$TaskName = 'InstagramContentEngineWorker')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'register_task_scheduler.ps1') -TaskName $TaskName
