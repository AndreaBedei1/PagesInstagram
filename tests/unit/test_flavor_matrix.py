"""Which (flavor, upload_method) pairs Meta actually implements.

The project shipped as ``instagram_login`` + ``resumable`` and described itself
as uploading local files straight to Meta with no public hosting. That pair does
not exist. Every gate went green and the first real canary got, from Meta:

    Graph API error [100]: The parameter video_url is required

``upload_type=resumable`` was ignored and ``/media`` asked for what it always
asks for. The resumable-uploads reference says why in one line — "Only for apps
that have implemented Facebook Login for Business" — and the content-publishing
page says the alternative just as plainly: with Instagram Login "the media must
be hosted on a publicly accessible server at the time of the attempt".

So the pairing is now checked here, on this machine, before anything reaches the
network, in a sentence that says what to do about it.
"""
from __future__ import annotations

import pytest

from src.core.meta_api import (API_HOSTS, PERMISSIONS_BY_FLAVOR,
                               RESUMABLE_FLAVORS, TOKEN_KIND_BY_FLAVOR,
                               check_upload_method, supports_resumable)


@pytest.mark.parametrize("flavor,method,expected", [
    ("instagram_login", "resumable", False),    # the pair that failed for real
    ("instagram_login", "hosted_url", True),
    ("facebook_login", "resumable", True),
    ("facebook_login", "hosted_url", True),
])
def test_the_matrix(flavor, method, expected):
    ok, reason = check_upload_method(flavor, method)
    assert ok is expected, reason


def test_the_refusal_names_both_ways_out():
    """A gate that only says no costs an afternoon; this one says what to do."""
    ok, reason = check_upload_method("instagram_login", "resumable")
    assert not ok
    assert "facebook_login" in reason, "deve indicare la strada senza hosting"
    assert "hosted_url" in reason, "deve indicare la strada con hosting"
    assert "video_url" in reason, "deve spiegare che cosa chiede Meta"


def test_an_unknown_flavor_is_refused_not_guessed():
    ok, reason = check_upload_method("carrier_pigeon", "resumable")
    assert not ok and "sconosciuto" in reason


def test_an_unknown_upload_method_is_refused():
    ok, reason = check_upload_method("facebook_login", "smoke_signals")
    assert not ok and "sconosciuto" in reason


def test_only_facebook_login_supports_resumable():
    assert supports_resumable("facebook_login")
    assert not supports_resumable("instagram_login")
    assert RESUMABLE_FLAVORS == ("facebook_login",)


def test_the_permission_names_are_not_shared_between_flavors():
    """instagram_business_* belongs to Instagram Login and means nothing to a
    Page token; reusing the names by analogy is how this goes wrong twice."""
    ig = set(PERMISSIONS_BY_FLAVOR["instagram_login"])
    fb = set(PERMISSIONS_BY_FLAVOR["facebook_login"])
    assert not (ig & fb), "nessun permesso è comune ai due flavor"
    assert ig == {"instagram_business_basic", "instagram_business_content_publish"}
    assert fb == {"instagram_basic", "instagram_content_publish",
                  "pages_read_engagement"}


def test_each_flavor_has_a_host_and_a_token_kind():
    for flavor in API_HOSTS:
        assert flavor in PERMISSIONS_BY_FLAVOR
        assert flavor in TOKEN_KIND_BY_FLAVOR
    assert API_HOSTS["facebook_login"] == "graph.facebook.com"
    assert API_HOSTS["instagram_login"] == "graph.instagram.com"
    assert TOKEN_KIND_BY_FLAVOR["facebook_login"] == "Facebook Page access token"


#: Pages still on the pair that cannot work, listed one by one on purpose.
#:
#: All five shipped as instagram_login + resumable. Only the canary page is
#: being migrated now — it is the one with credentials and the one that has to
#: prove the flow — and the rest follow once it has. An enumerated exception
#: keeps that debt visible and makes the list shrink deliberately: a page that
#: gets migrated must be removed from here, and a new page cannot be added to
#: the repository in the broken state without someone typing its name.
NOT_YET_MIGRATED = {
    "curiosita_mondo_it", "parola_giorno_it", "oggi_nella_storia_it",
    "domanda_giorno_it",
}


def test_the_canary_page_uses_a_supported_pair(project_paths):
    """The page that publishes must be configured for a flow that exists."""
    from src.accounts import load_pages

    page = load_pages(project_paths).get("pensiero_essenziale_it")
    ok, reason = check_upload_method(page.instagram.api_flavor,
                                     page.publishing.upload_method)
    assert ok, reason


def test_no_page_is_misconfigured_outside_the_declared_backlog(project_paths):
    from src.accounts import load_pages

    bad = set()
    for page in load_pages(project_paths).all():
        ok, _ = check_upload_method(page.instagram.api_flavor,
                                    page.publishing.upload_method)
        if not ok:
            bad.add(page.page_id)
    assert bad <= NOT_YET_MIGRATED, (
        f"pagine rotte non dichiarate: {sorted(bad - NOT_YET_MIGRATED)}")
    stale = NOT_YET_MIGRATED - bad
    assert not stale, (
        f"queste pagine risultano migrate: toglile da NOT_YET_MIGRATED "
        f"{sorted(stale)}")


def test_the_resumable_container_call_never_sends_a_video_url():
    """The whole point of resumable is that no URL exists to send."""
    import inspect

    from src.publishing.graph_client import GraphClient

    source = inspect.getsource(GraphClient.create_resumable_container)
    assert "upload_type" in source and "resumable" in source
    assert "video_url" not in source
