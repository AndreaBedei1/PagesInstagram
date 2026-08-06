"""The five-page dry-run, the publish guards and the Graph-API safety rails.

These are the tests that stand between a refactor and an accidental post.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.cli.instagram_cmds import upload_would_publish
from src.core.enums import Mode
from src.core.paths import Paths
from src.core.settings import load_settings
from src.scheduling.smoke_test import EXPECTED_PAGES, run_smoke_test
from src.security.scan import scan_repository, scan_text

ROOT = Path(__file__).resolve().parents[2]
SMOKE_DATE = "2026-08-07"


# ---------------------------------------------------------------------------
# The reproducible five-page dry-run
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def smoke_result():
    paths = Paths.create()
    settings = load_settings(paths)
    settings.mode = Mode.DRY_RUN
    return run_smoke_test(settings, local_date=SMOKE_DATE, paths=paths)


def test_smoke_test_passes_every_check(smoke_result):
    failed = [f"{c.name}: {c.detail}" for c in smoke_result.checks if not c.ok]
    assert not failed, "; ".join(failed)


def test_smoke_test_publishes_exactly_five(smoke_result):
    assert smoke_result.published_first_tick == EXPECTED_PAGES
    assert len(smoke_result.pages) == EXPECTED_PAGES


def test_smoke_test_publishes_no_stories(smoke_result):
    assert all(m["media_type"] == "reel" for m in smoke_result.media)


def test_smoke_test_is_idempotent(smoke_result):
    assert smoke_result.published_second_tick == 0


def test_smoke_test_uses_resumable_upload_only(smoke_result):
    assert {m["upload_method"] for m in smoke_result.media} == {"resumable"}


def test_smoke_test_refuses_to_run_outside_dry_run():
    paths = Paths.create()
    settings = load_settings(paths)
    settings.mode = Mode.PRODUCTION
    result = run_smoke_test(settings, local_date=SMOKE_DATE, paths=paths)
    assert not result.ok
    assert result.checks[0].name == "modalita_dry_run"


# ---------------------------------------------------------------------------
# --no-publish must stay --no-publish
# ---------------------------------------------------------------------------
def test_no_publish_is_the_default_and_publishes_nothing():
    allowed, reason = upload_would_publish(Mode.TEST, publish=False, confirm=True)
    assert not allowed
    assert "--publish" in reason


def test_dry_run_cannot_be_overridden_by_a_flag():
    """The mode is the outer guard; no command-line flag may defeat it."""
    allowed, reason = upload_would_publish(Mode.DRY_RUN, publish=True, confirm=True)
    assert not allowed
    assert "dry_run" in reason


@pytest.mark.parametrize("mode", [Mode.TEST, Mode.PRODUCTION])
def test_publishing_outside_dry_run_needs_an_explicit_confirmation(mode):
    assert not upload_would_publish(mode, publish=True, confirm=False)[0]
    assert upload_would_publish(mode, publish=True, confirm=True)[0]


def test_exactly_one_combination_publishes():
    publishing = [
        (mode, publish, confirm)
        for mode in (Mode.DRY_RUN, Mode.TEST, Mode.PRODUCTION)
        for publish in (False, True)
        for confirm in (False, True)
        if upload_would_publish(mode, publish=publish, confirm=confirm)[0]
    ]
    # Only (test|production, publish, confirm) — two of twelve combinations.
    assert publishing == [(Mode.TEST, True, True), (Mode.PRODUCTION, True, True)]


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
def test_repository_has_no_secrets_and_no_runtime_artifacts():
    result = scan_repository(ROOT)
    assert not result.findings, [f.as_dict() for f in result.findings][:5]
    assert not result.tracked_env_files
    assert not result.forbidden_tracked, result.forbidden_tracked[:5]
    assert result.gitignore_ok, result.gitignore_issues


# The fake tokens below are assembled at runtime, never written as literals.
# A test file containing a realistic-looking token would make the scanner it
# tests fail on itself — which is exactly what happened the first time this was
# written, and is a decent argument that the scanner works.
_FAKE_META = "E" + "AA" + "".join(chr(65 + (i * 7) % 26) for i in range(30))
_FAKE_OPAQUE = "".join(chr(97 + (i * 5) % 26) for i in range(28))


@pytest.mark.parametrize("template,rule", [
    ("access_token={t}", "meta_access_token"),
    ("Authorization: OAuth {t}", "authorization_header"),
    ("https://graph.instagram.com/me?access_token={t}", "token_in_query_string"),
    ('{{"access_token": "{t}"}}', "token_json_field"),
])
def test_scanner_catches_the_ways_a_token_leaks(template, rule):
    line = template.format(t=_FAKE_META)
    found = scan_text(line, "test")
    assert any(f.rule == rule for f in found), [f.rule for f in found]


def test_scanner_never_echoes_the_secret_it_found():
    line = "META_ACCESS" + "_TOKEN=" + _FAKE_META
    findings = scan_text(line, "test")
    assert findings
    for f in findings:
        assert _FAKE_META not in f.excerpt
        assert "***" in f.excerpt


def test_bearer_token_outside_an_authorization_header_is_caught():
    found = scan_text(f"curl -H 'Bearer {_FAKE_OPAQUE}'", "test")
    assert any(f.rule == "bearer_token" for f in found), [f.rule for f in found]


def test_this_test_file_does_not_itself_trip_the_scanner():
    """No literal credential-shaped string may live in the test suite."""
    for path in (Path(__file__), ROOT / "tests" / "unit" / "test_evergreen.py"):
        findings = scan_text(path.read_text(encoding="utf-8"), path.name)
        assert not findings, [f.as_dict() for f in findings]


def test_placeholders_are_not_reported():
    for benign in ("META_ACCESS_TOKEN=", "ACCESS_TOKEN=<TOKEN>",
                   "app_secret=change-me", "ICE_X_ACCESS_TOKEN=your-token-here"):
        assert not scan_text(benign, "test"), benign


# ---------------------------------------------------------------------------
# The local model must never degrade silently in production
# ---------------------------------------------------------------------------
def test_fallback_background_is_forbidden_in_production():
    settings = load_settings(Paths.create())
    assert settings.comfyui.allow_fallback_in_production is False
    settings.mode = Mode.PRODUCTION
    assert settings.comfyui_fallback_allowed() is False


def test_production_always_requires_reviewed_content():
    """No configuration file may let un-reviewed content reach a real account."""
    settings = load_settings(Paths.create())
    settings.mode = Mode.PRODUCTION
    settings.content.dataset_mode = "development_dataset"
    assert settings.require_production_ready() is True


def test_development_dataset_mode_is_the_default_outside_production():
    settings = load_settings(Paths.create())
    assert settings.mode == Mode.DRY_RUN
    assert settings.content.dataset_mode == "development_dataset"
    assert settings.require_production_ready() is False


# ---------------------------------------------------------------------------
# The review verdict file is the only source of human verdicts
# ---------------------------------------------------------------------------
def test_review_verdicts_declare_reviewer_method_and_sample():
    path = ROOT / "review" / "editorial_verdicts_20260805.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["reviewer"] and payload["reviewed_at"]
    assert "campione" in payload["method"].lower()
    assert payload["sample"]["total"] == 500
    reviewed = sum(len(v) for v in payload["verdicts"].values())
    assert reviewed >= 500
    # It must not claim to cover the whole corpus.
    assert reviewed < 5000
