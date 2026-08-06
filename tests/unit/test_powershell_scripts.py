"""Every PowerShell script must parse.

Nothing had ever parsed them, and `install_windows.ps1` — the entry point the
README told people to run — had an unterminated string and could not be
executed at all. A test that only runs where PowerShell exists is still worth
having: it runs on the machine that actually uses these scripts.

Parsing is not execution: none of these scripts is run here, and the ones that
would publish are not run anywhere without an explicit confirmation.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "scripts").glob("*.ps1"))

#: Scripts that must exist for the documented production path to work.
REQUIRED = {
    "setup_production.ps1", "preflight.ps1", "go_live_canary.ps1",
    "arm_page.ps1", "arm_all_pages.ps1", "install_worker.ps1",
    "start_worker.ps1", "stop_worker.ps1", "status.ps1", "rollback.ps1",
}

_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
needs_powershell = pytest.mark.skipif(
    _POWERSHELL is None, reason="PowerShell non disponibile su questa macchina")


def test_every_required_script_exists():
    present = {p.name for p in SCRIPTS}
    assert REQUIRED <= present, f"mancano: {sorted(REQUIRED - present)}"


@needs_powershell
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_parses(script: Path):
    command = (
        "$errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$errors); "
        "if ($errors.Count) { "
        "  $errors | Select-Object -First 1 | ForEach-Object { "
        "    Write-Output ('riga ' + $_.Extent.StartLineNumber + ': ' + $_.Message) }; "
        "  exit 1 } "
        "else { exit 0 }")
    result = subprocess.run([_POWERSHELL, "-NoProfile", "-Command", command],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"{script.name}: {result.stdout.strip()}"


def test_no_script_publishes_without_confirmation():
    """The canary is the only script that may publish, and it must ask first."""
    canary = (ROOT / "scripts" / "go_live_canary.ps1").read_text(encoding="utf-8")
    assert "publish-job" in canary
    assert "Read-Host" in canary, "il canary deve chiedere conferma esplicita"
    assert "PUBBLICA" in canary

    for script in SCRIPTS:
        if script.name == "go_live_canary.ps1":
            continue
        body = script.read_text(encoding="utf-8")
        assert "publish-job" not in body, (
            f"{script.name} pubblicherebbe senza passare dal canary")


def test_arming_scripts_do_not_change_mode():
    """Arming is not the same decision as switching to production.

    Looks for an assignment, not a mention: these scripts explain in their own
    documentation that ICE_MODE alone never publishes, and an earlier version of
    this test failed on that sentence.
    """
    import re

    def code_only(text: str) -> str:
        """Strip the <# … #> block comment and every line comment."""
        without_block = re.sub(r"<#.*?#>", " ", text, flags=re.S)
        return "\n".join(line.split("#", 1)[0]
                         for line in without_block.splitlines())

    assignment = re.compile(r"\$env:ICE_MODE\s*=|ICE_MODE\s*=\s*['\"]?production")
    for name in ("arm_page.ps1", "arm_all_pages.ps1"):
        body = code_only((ROOT / "scripts" / name).read_text(encoding="utf-8"))
        assert not assignment.search(body), (
            f"{name} non deve impostare ICE_MODE: armare e passare in "
            f"produzione sono due decisioni distinte")
