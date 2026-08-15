"""What the PowerShell wrappers *decide*, not what they contain.

The previous tests checked that these scripts existed, parsed, and mentioned a
confirmation word somewhere. All three were true of a preflight that passed with
a failing health check, and of a canary that could not publish at all.

So these run the scripts, with a stub standing in for the interpreter. The stub
records every command line it is given and returns whatever exit code the test
asks for, which is enough to drive every branch that matters: the preflight
verdict, the three canary levels, and what each wrapper does and does not call.

Nothing here reaches Meta, and nothing here touches the real database: the
scripts only ever see the stub.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
needs_powershell = pytest.mark.skipif(
    _POWERSHELL is None, reason="PowerShell non disponibile su questa macchina")

#: The variables preflight requires before it will check anything else. The
#: values are placeholders on purpose — the stub never uses them, and the
#: secret scanner has to keep passing on this file.
CREDENTIAL_ENV = {
    "META_GRAPH_API_VERSION": "v25.0",
    **{f"ICE_{page}_{field}": "placeholder"
       for page in ("PENSIERO_ESSENZIALE_IT", "CURIOSITA_MONDO_IT",
                    "PAROLA_GIORNO_IT", "OGGI_NELLA_STORIA_IT",
                    "DOMANDA_GIORNO_IT")
       for field in ("IG_USER_ID", "ACCESS_TOKEN")},
}

STUB = r"""
$line = ($args -join ' ')
Add-Content -LiteralPath $env:ICE_STUB_LOG -Value $line
if ($line -like '*canary-plan*--json*') {
    Write-Output $env:ICE_STUB_PLAN
}
$code = 0
if ($env:ICE_STUB_CODES) {
    foreach ($pair in $env:ICE_STUB_CODES.Split(';')) {
        if (-not $pair) { continue }
        $parts = $pair.Split('=')
        if ($line -like ('*' + $parts[0] + '*')) { $code = [int]$parts[1] }
    }
}
exit $code
"""

DEFAULT_PLAN = (
    '{"page_id":"pensiero_essenziale_it","job_id":42,'
    '"scheduled_at":"2099-01-01T08:00:00Z","output_path":"C:\\\\x\\\\reel.mp4",'
    '"audit_ok":true,"problems":[],"caption":"c","ig_user_id_set":true,'
    '"access_token_set":true,"container_id":null,"already_uploaded":false}'
)


class Run:
    """One script invocation, plus every command line the stub was handed."""

    def __init__(self, returncode: int, stdout: str, calls: list[str]):
        self.returncode = returncode
        self.stdout = stdout
        self.calls = calls

    def count(self, needle: str) -> int:
        return sum(1 for c in self.calls if needle in c)

    def called(self, needle: str) -> bool:
        return self.count(needle) > 0


def run_script(tmp_path: Path, script: str, *args: str, codes: str = "",
               plan: str = DEFAULT_PLAN, stdin: str = "",
               credentials: bool = True) -> Run:
    stub = tmp_path / "python_stub.ps1"
    stub.write_text(STUB, encoding="ascii")
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")

    env = dict(os.environ)
    env.pop("ICE_MODE", None)
    if credentials:
        env.update(CREDENTIAL_ENV)
    else:
        for key in CREDENTIAL_ENV:
            env.pop(key, None)
    env.update({"ICE_STUB_LOG": str(log), "ICE_STUB_CODES": codes,
                "ICE_STUB_PLAN": plan})

    # An empty .env, so the operator's real one is invisible to the test — and,
    # for rollback.ps1, untouched. These scripts run for real against the real
    # scripts directory; the Python stub catches the CLI calls but not a
    # PowerShell rewrite of a file, and rollback.ps1 rewrites ICE_MODE. Without
    # this seam every full test run quietly returned a production machine to
    # dry_run, which is precisely the kind of silent stop this suite exists to
    # prevent.
    env_file = tmp_path / "dotenv"
    env_file.write_text("ICE_MODE=production\n", encoding="utf-8")
    extra = (["-EnvFile", str(env_file)]
             if script in ("preflight.ps1", "rollback.ps1") else [])

    result = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(SCRIPTS / script), "-PythonPath", str(stub), *extra, *args],
        input=stdin, capture_output=True, text=True, timeout=180, env=env,
        cwd=str(ROOT))
    calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
    return Run(result.returncode, result.stdout + result.stderr, calls)


# =========================================================================
# 1. Preflight — the health check decides, and a warning is not a pass
# =========================================================================
@needs_powershell
def test_preflight_fails_when_the_health_check_reports_a_failure(tmp_path):
    run = run_script(tmp_path, "preflight.ps1", "-SkipCorpus",
                     codes="health-check=1")
    assert run.returncode == 1
    assert "PREFLIGHT FALLITO" in run.stdout
    assert "PREFLIGHT SUPERATO" not in run.stdout


@needs_powershell
def test_preflight_fails_when_the_health_check_reports_only_warnings(tmp_path):
    """Exit 2 is "warnings only" and it still blocks the first go-live."""
    run = run_script(tmp_path, "preflight.ps1", "-SkipCorpus",
                     codes="health-check=2")
    assert run.returncode == 1
    assert "PREFLIGHT FALLITO" in run.stdout


@needs_powershell
def test_preflight_passes_when_everything_is_green(tmp_path):
    run = run_script(tmp_path, "preflight.ps1", "-SkipCorpus")
    assert run.returncode == 0, run.stdout
    assert "PREFLIGHT SUPERATO" in run.stdout
    assert run.called("health-check --all")


@needs_powershell
def test_preflight_without_credentials_stops_at_two(tmp_path):
    """Not a failure: nothing has been filled in yet, and it says which."""
    run = run_script(tmp_path, "preflight.ps1", "-SkipCorpus", credentials=False)
    assert run.returncode == 2
    assert "ICE_PENSIERO_ESSENZIALE_IT_ACCESS_TOKEN" in run.stdout
    assert not run.called("health-check")


@needs_powershell
def test_preflight_does_not_require_the_app_credentials(tmp_path):
    """META_APP_ID/SECRET are not needed to publish, so they must not block."""
    env_free = {k: v for k, v in CREDENTIAL_ENV.items()}
    assert "META_APP_ID" not in env_free and "META_APP_SECRET" not in env_free
    run = run_script(tmp_path, "preflight.ps1", "-SkipCorpus")
    assert run.returncode == 0, run.stdout


@needs_powershell
def test_production_profile_is_stricter_about_comfyui_and_the_buffer(tmp_path):
    canary = run_script(tmp_path, "preflight.ps1", "-SkipCorpus",
                        codes="comfyui=1;buffer-status=1")
    assert canary.returncode == 0, canary.stdout
    assert "AVVISI" in canary.stdout

    production = run_script(tmp_path, "preflight.ps1", "-SkipCorpus",
                            "-Production", codes="comfyui=1;buffer-status=1")
    assert production.returncode == 1
    assert "PREFLIGHT FALLITO" in production.stdout


# =========================================================================
# 2. The canary, one level at a time
# =========================================================================
@needs_powershell
def test_whatif_makes_no_meta_calls_at_all(tmp_path):
    run = run_script(tmp_path, "go_live_canary.ps1", "-WhatIf")
    assert run.returncode == 0, run.stdout
    assert run.called("canary-plan")
    for forbidden in ("canary-upload", "publish-canary", "publish-job",
                      "upload-test", "health-check", "create-container"):
        assert not run.called(forbidden), f"-WhatIf ha invocato {forbidden}"
    assert "0 chiamate Meta" in run.stdout


@needs_powershell
def test_upload_only_uploads_and_never_publishes(tmp_path):
    run = run_script(tmp_path, "go_live_canary.ps1", "-UploadOnly")
    assert run.returncode == 0, run.stdout
    assert run.count("canary-upload") == 1
    assert not run.called("publish-canary")
    assert not run.called("publish-job")
    assert "UPLOAD META RIUSCITO" in run.stdout
    assert "MEDIA NON PUBBLICATO" in run.stdout


@needs_powershell
def test_whatif_and_uploadonly_together_are_refused(tmp_path):
    run = run_script(tmp_path, "go_live_canary.ps1", "-WhatIf", "-UploadOnly")
    assert run.returncode == 1
    assert not run.calls


@needs_powershell
def test_canary_without_the_word_publishes_nothing(tmp_path):
    run = run_script(tmp_path, "go_live_canary.ps1", stdin="no\n")
    assert run.returncode == 0, run.stdout
    assert not run.called("publish-canary")
    assert "Annullato" in run.stdout


@needs_powershell
def test_canary_with_the_word_publishes_exactly_once(tmp_path):
    run = run_script(tmp_path, "go_live_canary.ps1", stdin="PUBBLICA\n")
    assert run.returncode == 0, run.stdout
    assert run.count("publish-canary") == 1, run.calls
    assert run.count("canary-upload") == 1, "un solo upload, un solo container"
    assert not run.called("publish-job"), (
        "il canary non deve passare dal publisher normale")


@needs_powershell
def test_canary_reuses_an_existing_container_instead_of_uploading_again(tmp_path):
    plan = DEFAULT_PLAN.replace('"container_id":null', '"container_id":"C1"') \
                       .replace('"already_uploaded":false', '"already_uploaded":true')
    run = run_script(tmp_path, "go_live_canary.ps1", stdin="PUBBLICA\n", plan=plan)
    assert run.returncode == 0, run.stdout
    assert not run.called("canary-upload"), "non si ricarica lo stesso Reel"
    assert run.count("publish-canary") == 1


@needs_powershell
def test_canary_never_arms_the_page(tmp_path):
    run = run_script(tmp_path, "go_live_canary.ps1", stdin="PUBBLICA\n")
    assert not run.called("arm-page"), "armare è una decisione separata"
    assert "DISARMATA" in run.stdout


@needs_powershell
def test_canary_stops_when_the_preflight_fails(tmp_path):
    run = run_script(tmp_path, "go_live_canary.ps1", stdin="PUBBLICA\n",
                     codes="health-check=1")
    assert run.returncode == 1
    assert not run.called("publish-canary")
    assert not run.called("canary-upload")


# =========================================================================
# 3. The remaining wrappers
# =========================================================================
@needs_powershell
def test_arm_page_arms_exactly_the_page_it_was_given(tmp_path):
    run = run_script(tmp_path, "arm_page.ps1", "pensiero_essenziale_it")
    assert run.returncode == 0, run.stdout
    assert run.count("arm-page pensiero_essenziale_it") == 1
    assert not run.called("--disarm")
    assert not run.called("arm-page all")


@needs_powershell
def test_arm_page_disarm_passes_the_flag(tmp_path):
    run = run_script(tmp_path, "arm_page.ps1", "pensiero_essenziale_it", "-Disarm")
    assert run.count("arm-page pensiero_essenziale_it --disarm") == 1


@needs_powershell
def test_rollback_stops_the_worker_disarms_every_page_and_restores_dry_run(tmp_path):
    run = run_script(tmp_path, "rollback.ps1")
    assert run.count("arm-page all --disarm") == 1, run.calls
    assert run.called("status")
    assert not run.called("publish"), "un rollback non pubblica nulla"


@needs_powershell
def test_arm_all_pages_requires_the_word(tmp_path):
    run = run_script(tmp_path, "arm_all_pages.ps1", stdin="no\n")
    assert run.returncode == 0
    assert not run.called("arm-page all"), "senza ARMA non si arma niente"


# ---- the suite must not touch the machine it runs on ----------------------
def test_no_script_rewrites_the_operators_env_file(tmp_path):
    """A test run must never change what the machine does in production.

    rollback.ps1 rewrites ICE_MODE, in plain PowerShell that the Python stub
    cannot intercept, and the tests run it for real. So every full run put a
    live machine back into dry_run: the worker kept ticking, the health check
    stayed green, and nothing was ever published again — the failure mode this
    whole suite is meant to catch, caused by the suite itself.
    """
    real_env = ROOT / ".env"
    before = real_env.read_bytes() if real_env.exists() else None

    run = run_script(tmp_path, "rollback.ps1")
    assert run.returncode == 0, run.stdout

    after = real_env.read_bytes() if real_env.exists() else None
    assert after == before, (
        "rollback.ps1 ha riscritto il .env reale durante i test")

    # and it did rewrite the file it was given, so the seam is real
    assert "dry_run" in (tmp_path / "dotenv").read_text(encoding="utf-8")
