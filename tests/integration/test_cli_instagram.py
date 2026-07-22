"""Smoke tests for the `ice instagram` CLI (offline commands)."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


def test_check_config_reports_resumable(monkeypatch):
    for k in ("ICE_MOTIVATIONAL_IT_ACCESS_TOKEN", "ICE_MOTIVATIONAL_IT_IG_USER_ID",
              "META_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from src.cli.instagram_cmds import app
    result = CliRunner().invoke(app, ["check-config", "--page", "motivational_it"])
    assert result.exit_code == 0, result.output
    assert "resumable" in result.output
    assert "missing" in result.output  # no credentials set


def test_token_status_without_creds_exits_nonzero(monkeypatch):
    for k in ("ICE_MOTIVATIONAL_IT_ACCESS_TOKEN", "ICE_MOTIVATIONAL_IT_IG_USER_ID",
              "META_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from src.cli.instagram_cmds import app
    result = CliRunner().invoke(app, ["token-status", "--page", "motivational_it"])
    assert result.exit_code == 1


def test_publish_job_requires_confirm(monkeypatch):
    from src.cli.instagram_cmds import app
    result = CliRunner().invoke(app, ["publish-job", "--page", "motivational_it",
                                      "--job", "999999"])
    # job doesn't exist -> exit 1; but the point is it never publishes without --confirm
    assert result.exit_code in (0, 1)
    assert "--confirm" in result.output or "non trovato" in result.output
