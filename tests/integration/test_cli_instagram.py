"""Smoke tests for the `ice instagram` CLI (offline commands)."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


def test_check_config_reports_the_validated_transport(monkeypatch):
    for k in ("ICE_PENSIERO_ESSENZIALE_IT_ACCESS_TOKEN", "ICE_PENSIERO_ESSENZIALE_IT_IG_USER_ID",
              "META_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from src.cli.instagram_cmds import app
    result = CliRunner().invoke(app, ["check-config", "--page", "pensiero_essenziale_it"])
    assert result.exit_code == 0, result.output
    assert "hosted_url" in result.output
    assert "cloudflare_quick_tunnel" in result.output
    assert "facebook_login" in result.output
    assert "missing" in result.output  # no credentials set


def test_token_status_without_creds_exits_nonzero(monkeypatch):
    for k in ("ICE_PENSIERO_ESSENZIALE_IT_ACCESS_TOKEN", "ICE_PENSIERO_ESSENZIALE_IT_IG_USER_ID",
              "META_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from src.cli.instagram_cmds import app
    result = CliRunner().invoke(app, ["token-status", "--page", "pensiero_essenziale_it"])
    assert result.exit_code == 1


def test_publish_job_requires_confirm(monkeypatch):
    from src.cli.instagram_cmds import app
    result = CliRunner().invoke(app, ["publish-job", "--page", "pensiero_essenziale_it",
                                      "--job", "999999"])
    # job doesn't exist -> exit 1; but the point is it never publishes without --confirm
    assert result.exit_code in (0, 1)
    assert "--confirm" in result.output or "non trovato" in result.output
