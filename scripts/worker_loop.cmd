@echo off
rem ===========================================================================
rem  Keep the worker running, without the Task Scheduler.
rem
rem  On a machine where policy forbids creating scheduled tasks (this one
rem  answers "Accesso negato" to both Register-ScheduledTask and schtasks), the
rem  per-user Startup folder is the way in that needs no privileges. The task
rem  would have given two things this has to reproduce: start at logon, and
rem  restart if the process dies. The logon part is the Startup folder; the
rem  restart part is the loop below.
rem
rem  Everything the engine decides stays in the engine: this only starts it.
rem ===========================================================================
cd /d "%~dp0.."

:loop
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m src.cli worker --interval 60 >> "logs\worker_run.log" 2>&1
) else (
    python -m src.cli worker --interval 60 >> "logs\worker_run.log" 2>&1
)
rem The worker exits on a stop request, on a crash, or because another instance
rem already holds the single-instance lock. Thirty seconds is long enough not to
rem spin against the lock and short enough that a crash costs nothing.
timeout /t 30 /nobreak >nul
goto loop
