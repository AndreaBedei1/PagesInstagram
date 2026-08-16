<#
.SYNOPSIS
    Start the worker at every logon, without the Task Scheduler.

.DESCRIPTION
    install_worker.ps1 registers a scheduled task, which is the better mechanism
    when it is available: it can restart the worker on failure and catch up if
    the PC was off. On a machine where policy forbids creating tasks it answers

        Register-ScheduledTask : Accesso negato.   (HRESULT 0x80070005)

    and schtasks.exe answers the same, because both go through the same service.
    Nothing about that is fixable from here, and it needs no administrator to
    work around: the per-user Startup folder starts a program at logon, and a
    loop restarts it if it dies. That is what the task would have given.

    What is installed is one line in the Startup folder — a .vbs that launches
    scripts\worker_loop.cmd with no visible window. Nothing is copied, so the
    repository stays the single source: update the code and the next start picks
    it up.

    Removal: unregister_task_scheduler.ps1 (it removes both mechanisms), or
    delete the file this prints.

.EXAMPLE
    .\scripts\install_worker_startup.ps1
    .\scripts\install_worker_startup.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param([string]$Name = 'InstagramContentEngineWorker')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$loop = Join-Path $root 'scripts\worker_loop.cmd'
if (-not (Test-Path $loop)) { throw "manca $loop" }

$startup = [Environment]::GetFolderPath('Startup')
if (-not $startup -or -not (Test-Path $startup)) {
    throw "cartella Esecuzione automatica non trovata: $startup"
}
$vbs = Join-Path $startup "$Name.vbs"

# 0 = hidden window, False = do not wait. Written in ASCII: this file is read by
# wscript.exe under the system codepage, not by PowerShell.
$content = @"
' Avvia il worker dell'Instagram Content Engine a ogni accesso, senza finestra.
' Generato da scripts\install_worker_startup.ps1 — modifica quello, non questo.
Set sh = CreateObject("WScript.Shell")
sh.Run """$loop""", 0, False
"@

if ($PSCmdlet.ShouldProcess($vbs, 'creare l''avvio automatico')) {
    Set-Content -Path $vbs -Value $content -Encoding ASCII
    Write-Host "Avvio automatico installato per l'utente corrente." -ForegroundColor Green
    Write-Host "  file   : $vbs"
    Write-Host "  esegue : $loop"
    Write-Host ""
    Write-Host "Parte a ogni accesso e si riavvia da solo se il processo muore."
    Write-Host "Il PC deve essere acceso e l'utente collegato all'orario di"
    Write-Host "pubblicazione; se era spento, il worker applica la"
    Write-Host "missed_job_policy della pagina (240 minuti di recupero)."
    Write-Host ""
    Write-Host "Avvio immediato:  wscript.exe `"$vbs`""
    Write-Host "Rimozione:        .\scripts\unregister_task_scheduler.ps1"
}
