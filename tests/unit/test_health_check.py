"""The five verdicts, one axis at a time.

The old health check had two counters, so "I could not reach Meta" and "Meta
says this token is dead" produced the same yellow row. They call for opposite
actions — wait, or regenerate the token — and the first go-live has to be able
to tell them apart.

Responses here are shaped like Meta's real ones: ``/me`` returns the user node,
``/debug_token`` returns ``{"data": {"is_valid", "expires_at", "scopes"}}``, and
errors arrive as PublishError with the code Meta would have sent.
"""
from __future__ import annotations

import time

import pytest

from src.core.errors import PublishError
from src.core.meta_api import REQUIRED_PERMISSIONS
from src.core.settings import load_settings
from src.publishing.health import Verdict, check_page, check_pages
from src.publishing.publisher import PublishTarget

PAGE_ID = "pensiero_essenziale_it"


class FakeClient:
    """Only what the health check calls, and each answer independently set."""

    def __init__(self, *, me=None, me_error=None, days=59, valid=True,
                 scopes=REQUIRED_PERMISSIONS, debug_error=None,
                 account_error=None, account_type="business"):
        self._me = me if me is not None else {
            "user_id": "17841400000000000", "username": "pensiero.essenziale",
            "account_type": account_type}
        self._me_error = me_error
        self._days = days
        self._valid = valid
        self._scopes = scopes
        self._debug_error = debug_error
        self._account_error = account_error

    def verify_token(self):
        if self._me_error:
            raise self._me_error
        return self._me

    def debug_token(self, app_id, app_secret):
        if self._debug_error:
            raise self._debug_error
        expires = 0 if self._days is None else int(time.time()) + self._days * 86400
        return {"data": {"app_id": app_id, "is_valid": self._valid,
                         "expires_at": expires, "scopes": list(self._scopes)}}

    def get_account_info(self, ig_user_id):
        if self._account_error:
            raise self._account_error
        return self._me

    def get_publishing_limit(self, ig_user_id):
        return {"data": [{"quota_usage": 0,
                          "config": {"quota_total": 100, "quota_duration": 86400}}]}


@pytest.fixture
def page(project_paths):
    from src.accounts import load_pages
    return load_pages(project_paths).get(PAGE_ID)


@pytest.fixture
def settings(project_paths):
    return load_settings(project_paths, load_dotenv=False)


@pytest.fixture
def with_app_credentials(monkeypatch):
    monkeypatch.setenv("META_APP_ID", "1234567890")
    monkeypatch.setenv("META_APP_SECRET", "placeholder-not-a-secret")


@pytest.fixture
def without_app_credentials(monkeypatch):
    monkeypatch.delenv("META_APP_ID", raising=False)
    monkeypatch.delenv("META_APP_SECRET", raising=False)


def _check(settings, page, client, **kw):
    return check_page(settings, page, target=PublishTarget(client, "ig"), **kw)


# ---- the happy path --------------------------------------------------------
def test_everything_green(settings, page, with_app_credentials):
    health = _check(settings, page, FakeClient())
    assert health.token == Verdict.VALID
    assert health.expiry == Verdict.OK and health.days_left >= 50
    assert health.permissions == Verdict.OK
    assert health.account == Verdict.OK
    assert health.ok and not health.failures and not health.warnings


# ---- token axis ------------------------------------------------------------
def test_meta_refusing_the_token_is_a_failure(settings, page, with_app_credentials):
    client = FakeClient(me_error=PublishError(
        "Graph API error [190]: Error validating access token",
        retryable=False, code="190"))
    health = _check(settings, page, client)
    assert health.token == Verdict.INVALID
    assert any("rifiutato" in f for f in health.failures)


def test_not_being_able_to_ask_is_a_different_failure(settings, page,
                                                      with_app_credentials):
    """A 500 or a dead connection is not evidence that the token is bad."""
    client = FakeClient(me_error=PublishError("network error: timeout",
                                              retryable=True))
    health = _check(settings, page, client)
    assert health.token == Verdict.UNVERIFIABLE
    assert any("non verificabile" in f for f in health.failures)
    assert not any("rifiutato" in f for f in health.failures)


def test_debug_token_saying_invalid_overrides_a_working_me(settings, page,
                                                           with_app_credentials):
    health = _check(settings, page, FakeClient(valid=False))
    assert health.token == Verdict.INVALID


# ---- expiry axis -----------------------------------------------------------
def test_expiry_is_unknown_without_the_app_credentials(settings, page,
                                                       without_app_credentials):
    """Not a failure, but not a pass either: the go-live treats it as blocking."""
    health = _check(settings, page, FakeClient())
    assert health.token == Verdict.VALID
    assert health.expiry == Verdict.UNKNOWN
    assert health.days_left is None
    assert not health.failures
    assert any("META_APP_ID" in w for w in health.warnings)
    assert not health.ok


def test_a_token_close_to_expiry_warns(settings, page, with_app_credentials):
    health = _check(settings, page, FakeClient(days=5))
    assert health.expiry == Verdict.EXPIRING
    assert health.days_left <= 5
    assert not health.failures and health.warnings


def test_an_expired_token_fails(settings, page, with_app_credentials):
    health = _check(settings, page, FakeClient(days=-3))
    assert health.expiry == Verdict.EXPIRED
    assert any("scaduto" in f for f in health.failures)


def test_the_warning_window_is_configurable(settings, page, with_app_credentials):
    assert _check(settings, page, FakeClient(days=20)).expiry == Verdict.OK
    assert _check(settings, page, FakeClient(days=20),
                  warn_days=30).expiry == Verdict.EXPIRING


def test_debug_token_unavailable_is_a_warning_not_a_verdict(settings, page,
                                                            with_app_credentials):
    client = FakeClient(debug_error=PublishError("Graph API error [100]: bad app",
                                                 retryable=False, code="100"))
    health = _check(settings, page, client)
    assert health.token == Verdict.VALID       # /me still answered
    assert health.expiry == Verdict.UNKNOWN
    assert any("debug_token" in w for w in health.warnings)


# ---- permissions axis ------------------------------------------------------
def test_a_missing_publishing_permission_fails(settings, page, with_app_credentials):
    health = _check(settings, page,
                    FakeClient(scopes=("instagram_business_basic",)))
    assert health.permissions == Verdict.MISSING
    assert any("instagram_business_content_publish" in f for f in health.failures)


def test_no_scopes_reported_is_unknown(settings, page, with_app_credentials):
    health = _check(settings, page, FakeClient(scopes=()))
    assert health.permissions == Verdict.UNKNOWN
    assert not health.failures and health.warnings


# ---- account axis ----------------------------------------------------------
def test_a_personal_account_fails(settings, page, with_app_credentials):
    health = _check(settings, page, FakeClient(account_type="personal"))
    assert health.account == Verdict.INCOMPATIBLE
    assert any("personale" in f for f in health.failures)


def test_a_creator_account_warns(settings, page, with_app_credentials):
    health = _check(settings, page, FakeClient(account_type="creator"))
    assert health.account == Verdict.INCOMPATIBLE
    assert not health.failures and health.warnings


def test_an_unreachable_account_fails(settings, page, with_app_credentials):
    client = FakeClient(account_error=PublishError("network error", retryable=True))
    health = _check(settings, page, client)
    assert health.account == Verdict.UNREACHABLE
    assert any("irraggiungibile" in f for f in health.failures)


# ---- credentials & exit codes ----------------------------------------------
def test_absent_credentials_fail_before_anything_else(settings, page):
    health = check_page(settings, page, factory=lambda _s, _p: None)
    assert health.credentials == Verdict.ABSENT
    assert any("IG_USER_ID" in f for f in health.failures)


def test_exit_code_is_zero_one_or_two(settings, project_paths, with_app_credentials):
    from src.accounts import load_pages

    pages = load_pages(project_paths).all()

    green = check_pages(settings, pages,
                        factory=lambda _s, _p: PublishTarget(FakeClient(), "ig"))
    assert green.exit_code == 0 and green.green

    warned = check_pages(settings, pages,
                         factory=lambda _s, _p: PublishTarget(FakeClient(days=3), "ig"))
    assert warned.exit_code == 2 and not warned.green

    failed = check_pages(
        settings, pages,
        factory=lambda _s, _p: PublishTarget(FakeClient(days=-1), "ig"))
    assert failed.exit_code == 1 and not failed.green


def test_no_token_or_secret_is_ever_returned(settings, page, with_app_credentials):
    """The report is rendered and logged; it must carry nothing to redact."""
    payload = repr(_check(settings, page, FakeClient()).as_dict())
    assert "placeholder-not-a-secret" not in payload
    assert "access_token" not in payload
