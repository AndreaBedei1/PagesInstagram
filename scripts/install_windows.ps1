<#
.SYNOPSIS
    Fresh Windows install. Kept as the documented entry point; the work happens
    in setup_production.ps1.

.DESCRIPTION
    The previous version of this file had an unterminated string and could not
    be parsed at all — a defect nothing caught, because no check had ever parsed
    the PowerShell scripts. tests/unit/test_powershell_scripts.py parses every
    one of them now.

    Rather than repair a script that duplicated setup_production.ps1, this
    delegates to it. Behaviour is unchanged for anyone following the README.

.EXAMPLE
    .\scripts\install_windows.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipDatasets,
    [switch]$SkipModel
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'setup_production.ps1') -SkipModel:$SkipModel
exit $LASTEXITCODE
