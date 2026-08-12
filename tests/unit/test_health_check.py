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
from src.core.meta_api import PERMISSIONS_BY_FLAVOR

#: The canary page is facebook_login, so its scopes are the Facebook set.
#: Checking one flavor's permission names against the other's token is
#: exactly the mistake this project already made once.
FLAVOR_SCOPES = PERMISSIONS_BY_FLAVOR["facebook_login"]
from src.core.settings import load_settings
from src.publishing.health import (ISSUED_AT_SUFFIX, Verdict, check_page,
                                   check_pages)
from src.publishing.publisher import PublishTarget

PAGE_ID = "pensiero_essenziale_it"


class FakeClient:
    """Only what the health check calls, and each answer independently set."""

    def __init__(self, *, me=None, me_error=None, days=59, valid=True,
                 scopes=FLAVOR_SCOPES, debug_error=None,
                 account_error=None, account_type="business", limit_error=None):
        self._limit_error = limit_error
        self._me = me if me is not None else {
            "user_id": "17841400000000000", "username": "pensiero.essenziale",
            "account_type": account_type}
        self._me_error = me_error
        self._days = days
        self._valid = valid
        self._scopes = scopes
        self._debug_error = debug_error
        self._account_error = account_error

    def verify_token(self, ig_user_id=None):
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
        if self._limit_error:
            raise self._limit_error
        return {"data": [{"quota_usage": 0,
                          "config": {"quota_total": 100, "quota_duration": 86400}}]}


@pytest.fixture
def page(project_paths):
    """The canary page, pinned to the project default upload method.

    The on-disk YAML is a live configuration that a canary may temporarily
    change; a test that inherits it asserts about whatever state the operator
    left behind. The hosted_url tests opt in explicitly instead.
    """
    from src.accounts import load_pages

    cfg = load_pages(project_paths).get(PAGE_ID)
    cfg.publishing.upload_method = "resumable"
    cfg.publishing.hosted_url_provider = "public_base_url"
    # Pinned together with the transport, and for the same reason: resumable is
    # a video protocol, so a page pinned to it is a page that publishes Reels.
    # Leaving the format to the live YAML made every test here fail the moment
    # the account migrated to image posts.
    cfg.publishing.feed_media_type = "REELS"
    return cfg


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


@pytest.fixture(autouse=True)
def no_declared_date(monkeypatch):
    """The declared date is a fallback; tests opt into it explicitly."""
    monkeypatch.delenv(f"ICE_{PAGE_ID.upper()}{ISSUED_AT_SUFFIX}", raising=False)


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
def test_expiry_is_unknown_without_any_source(settings, page,
                                              without_app_credentials):
    """Not a failure, but not a pass either — and it says how to clear it."""
    health = _check(settings, page, FakeClient())
    assert health.token == Verdict.VALID
    assert health.expiry == Verdict.UNKNOWN
    assert health.days_left is None
    assert not health.failures
    assert any(ISSUED_AT_SUFFIX in w for w in health.warnings), (
        "l'avviso deve dire come risolverlo, non solo che non si sa")
    assert not health.ok


# ---- the declared date, when the API will not answer -----------------------
def _declare(monkeypatch, page, days_ago: int) -> None:
    from datetime import date, timedelta as td
    monkeypatch.setenv(f"{page.env_prefix()}{ISSUED_AT_SUFFIX}",
                       (date.today() - td(days=days_ago)).isoformat())


def test_a_declared_issue_date_gives_an_expiry(settings, page, monkeypatch,
                                               without_app_credentials):
    _declare(monkeypatch, page, days_ago=5)
    health = _check(settings, page, FakeClient())
    assert health.expiry == Verdict.OK
    # .days truncates, so a partial day reads as one fewer. 60 documented
    # days minus the five declared, give or take the hour of the run.
    assert health.days_left in (54, 55)
    assert health.expiry_source == "dichiarata, non verificata"
    assert health.ok


def test_a_declared_date_still_blocks_a_token_near_expiry(settings, page,
                                                          monkeypatch,
                                                          without_app_credentials):
    """The protection survives the fallback — that was the whole point."""
    _declare(monkeypatch, page, days_ago=55)
    health = _check(settings, page, FakeClient())
    assert health.expiry == Verdict.EXPIRING
    assert not health.ok


def test_a_declared_date_in_the_past_fails(settings, page, monkeypatch,
                                           without_app_credentials):
    _declare(monkeypatch, page, days_ago=70)
    health = _check(settings, page, FakeClient())
    assert health.expiry == Verdict.EXPIRED
    assert health.failures


def test_a_malformed_declared_date_is_ignored_not_guessed(settings, page,
                                                          monkeypatch,
                                                          without_app_credentials):
    monkeypatch.setenv(f"{page.env_prefix()}{ISSUED_AT_SUFFIX}", "11/08/2026")
    health = _check(settings, page, FakeClient())
    assert health.expiry == Verdict.UNKNOWN
    assert any("non è una data" in w for w in health.warnings)


def test_debug_token_wins_over_the_declared_date(settings, page, monkeypatch,
                                                 with_app_credentials):
    """A measurement beats a declaration when both are available."""
    _declare(monkeypatch, page, days_ago=59)          # would say 1 day left
    health = _check(settings, page, FakeClient(days=40))
    assert health.days_left in (39, 40)
    assert health.expiry_source == "letta da debug_token"


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
    health = _check(settings, page, FakeClient(scopes=("instagram_basic",)))
    assert health.permissions == Verdict.MISSING
    assert any("instagram_content_publish" in f for f in health.failures)


def test_without_a_scope_list_the_publishing_node_is_probed(settings, page,
                                                            with_app_credentials):
    """Evidence beats a shrug: ask the node the publish call would use."""
    health = _check(settings, page, FakeClient(scopes=()))
    assert health.permissions == Verdict.OK
    assert "sonda" in health.permissions_source
    assert not health.failures


def test_a_probe_the_publishing_node_refuses_is_a_failure(settings, page,
                                                          with_app_credentials):
    """If that node says no, the publish call would say no too."""
    health = _check(settings, page, FakeClient(
        scopes=(), limit_error=PublishError("Graph API error [10]: no permission",
                                            retryable=False, code="10")))
    assert health.permissions == Verdict.MISSING
    assert any("rifiuta questo token" in f for f in health.failures)


def test_a_scope_list_is_preferred_to_the_probe(settings, page,
                                                with_app_credentials):
    health = _check(settings, page, FakeClient())
    assert health.permissions == Verdict.OK
    assert health.permissions_source == "elenco degli scope"


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

    # Only the migrated page, pinned to the project default upload method for
    # the same reason as the `page` fixture: this test is about exit codes.
    only = load_pages(project_paths).get(PAGE_ID)
    only.publishing.upload_method = "resumable"
    only.publishing.feed_media_type = "REELS"
    pages = [only]

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


# ---- notes never change the verdict ----------------------------------------
def test_a_debug_token_failure_covered_by_the_fallback_is_a_note(
        settings, page, monkeypatch, with_app_credentials):
    """Instagram Login: the endpoint never answers, and a fallback does.

    Reporting that as a warning would leave the preflight permanently blocked
    on something no action can fix, while telling the operator nothing they can
    do. It is still printed — as a note, which cannot change the exit code.
    """
    _declare(monkeypatch, page, days_ago=0)
    health = _check(settings, page, FakeClient(
        debug_error=PublishError("Graph API error [2]: (#2) Service temporarily "
                                 "unavailable", retryable=True, code="2")))
    assert health.expiry == Verdict.OK
    assert health.permissions == Verdict.OK
    assert not health.warnings and not health.failures
    assert any("debug_token" in n for n in health.notes)
    assert health.ok


def test_a_debug_token_failure_with_nothing_to_fall_back_on_still_warns(
        settings, page, without_app_credentials, monkeypatch):
    """Remove the fallback and the warning must come back."""
    monkeypatch.setenv("META_APP_ID", "1234567890")
    monkeypatch.setenv("META_APP_SECRET", "placeholder-not-a-secret")
    health = _check(settings, page, FakeClient(
        debug_error=PublishError("boom", retryable=True, code="2")))
    assert health.expiry == Verdict.UNKNOWN
    assert health.warnings and not health.ok


def test_the_report_says_where_the_expiry_came_from(settings, page, monkeypatch,
                                                    without_app_credentials):
    """A declared date and a measured one must never look the same."""
    _declare(monkeypatch, page, days_ago=1)
    declared = _check(settings, page, FakeClient()).as_dict()
    assert declared["expiry_source"] == "dichiarata, non verificata"

    monkeypatch.setenv("META_APP_ID", "1234567890")
    monkeypatch.setenv("META_APP_SECRET", "placeholder-not-a-secret")
    measured = _check(settings, page, FakeClient(days=30)).as_dict()
    assert measured["expiry_source"] == "letta da debug_token"
    assert declared["days_left"] != measured["days_left"]


# ---- the flavor matrix is checked before anything reaches the network ------
def test_an_impossible_flavor_pairing_fails_before_the_first_call(settings,
                                                                  project_paths):
    """instagram_login + resumable: refused here, not by Meta mid-upload.

    This is the configuration that shipped, passed every gate, and then got
    "The parameter video_url is required" from Meta during a real upload.
    """
    from src.accounts import load_pages

    page = load_pages(project_paths).get("curiosita_mondo_it")
    assert page.instagram.api_flavor == "instagram_login"
    assert page.publishing.upload_method == "resumable"

    class Exploding:
        def verify_token(self, *_a, **_k):
            raise AssertionError("nessuna chiamata deve partire")

    health = check_page(settings, page,
                        target=PublishTarget(Exploding(), "ig"))
    assert health.failures
    assert any("resumable" in f and "facebook_login" in f
               for f in health.failures)


def test_the_canary_page_is_checked_against_the_facebook_permissions(
        settings, page, with_app_credentials):
    """The names must follow the flavor, not habit."""
    assert page.instagram.api_flavor == "facebook_login"
    health = _check(settings, page, FakeClient(
        scopes=("instagram_basic", "instagram_content_publish",
                "pages_read_engagement")))
    assert health.permissions == Verdict.OK


def test_facebook_login_does_not_invent_an_account_type(settings, page,
                                                        monkeypatch,
                                                        without_app_credentials):
    """This flavor does not report it; the Page link is the evidence."""
    _declare(monkeypatch, page, days_ago=1)
    health = _check(settings, page, FakeClient(account_type=""))
    assert health.account_type == ""
    assert not health.warnings, health.warnings
    assert any("Pagina" in n for n in health.notes)


# ---- the two clocks on a Page token ---------------------------------------
def test_a_token_that_never_expires_is_a_fact_not_an_unknown(settings, page,
                                                             monkeypatch,
                                                             with_app_credentials):
    """expires_at = 0 means "no expiry", and falling back to a declared date
    would replace a fact with a guess."""
    _declare(monkeypatch, page, days_ago=59)      # would otherwise say 1 day
    health = _check(settings, page, FakeClient(days=None))
    assert health.expiry == Verdict.OK
    assert health.days_left is None
    assert "non scade" in health.expiry_source
    assert health.ok


def test_data_access_is_the_clock_that_still_runs(settings, page,
                                                  with_app_credentials):
    """A permanent token still loses data access, and is_valid stays true."""
    import time

    client = FakeClient(days=None)
    original = client.debug_token

    def with_deadline(app_id, app_secret, days):
        data = original(app_id, app_secret)
        data["data"]["data_access_expires_at"] = int(time.time()) + days * 86400
        return data

    client.debug_token = lambda a, s: with_deadline(a, s, 80)
    healthy = _check(settings, page, client)
    assert healthy.data_access_days is not None and healthy.data_access_days > 70
    assert healthy.ok

    client.debug_token = lambda a, s: with_deadline(a, s, 4)
    soon = _check(settings, page, client)
    assert soon.data_access_days <= 4
    assert any("accesso ai dati" in w for w in soon.warnings)
    assert not soon.ok


# ---- hosted_url: the risk is a missing host, not the method ----------------
def _hosted(settings, page, provider="public_base_url"):
    """hosted_url with a *static* host, unless the test says otherwise.

    The page ships configured for the Quick Tunnel, so a test about a missing
    base URL has to say which provider it means.
    """
    page.publishing.upload_method = "hosted_url"
    page.publishing.hosted_url_provider = provider
    return page


def test_hosted_url_without_a_public_base_url_fails(settings, page, monkeypatch,
                                                    with_app_credentials):
    settings.publishing.public_media_base_url = ""
    health = _check(settings, _hosted(settings, page), FakeClient())
    assert any("ICE_PUBLIC_MEDIA_BASE_URL" in f for f in health.failures)


def test_hosted_url_over_plain_http_fails(settings, page, with_app_credentials):
    settings.publishing.public_media_base_url = "http://example.com"
    health = _check(settings, _hosted(settings, page), FakeClient())
    assert any("https" in f for f in health.failures)


def test_hosted_url_with_a_public_https_base_is_a_note(settings, page,
                                                       with_app_credentials):
    """A configured host is a working configuration, not a warning."""
    settings.publishing.public_media_base_url = "https://example.trycloudflare.com"
    health = _check(settings, _hosted(settings, page), FakeClient())
    assert not health.warnings and not health.failures
    assert any("hosted_url" in n for n in health.notes)
    assert health.ok


def test_the_quick_tunnel_provider_needs_no_base_url(settings, page,
                                                     with_app_credentials):
    """Demanding a host would be asking for what this provider exists to avoid."""
    settings.publishing.public_media_base_url = ""
    page.publishing.upload_method = "hosted_url"
    page.publishing.hosted_url_provider = "cloudflare_quick_tunnel"
    health = _check(settings, page, FakeClient())
    assert not health.failures and not health.warnings
    assert any("Quick Tunnel" in n for n in health.notes)
    assert health.ok


# ---- the format and the transport have to agree ---------------------------
def test_an_image_post_cannot_use_the_resumable_upload(settings, page,
                                                       with_app_credentials):
    """Meta's resumable upload is a video protocol; a still has nothing to stream."""
    page.publishing.feed_media_type = "IMAGE"
    page.publishing.upload_method = "resumable"
    health = _check(settings, page, FakeClient())
    assert any("resumable" in f and "IMAGE" in f for f in health.failures)
    assert not health.ok


def test_an_image_post_over_a_tunnel_says_what_it_publishes(settings, page,
                                                            with_app_credentials):
    page.publishing.feed_media_type = "IMAGE"
    page.publishing.upload_method = "hosted_url"
    page.publishing.hosted_url_provider = "cloudflare_quick_tunnel"
    health = _check(settings, page, FakeClient())
    assert not health.failures and not health.warnings
    assert any("1080x1350" in n and "nessuna traccia audio" in n
               for n in health.notes)
