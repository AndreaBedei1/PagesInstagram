"""One container, one media_publish, and no tunnel open a moment longer.

The transport is tested next door; this is about the publisher that drives it.
The tunnel is replaced by a spy that records when it was open, so the tests can
assert the thing that actually matters operationally: nothing is listening when
nothing is being published.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PAGE_ID = "pensiero_essenziale_it"


class SpySession:
    """Stands in for TunnelSession and remembers how it was used."""

    opened = 0
    closed = 0
    open_now = False

    def __init__(self, media_path, paths, **kw):
        self.public_url = "https://spy.trycloudflare.com/media/abc.mp4"

    def __enter__(self):
        type(self).opened += 1
        type(self).open_now = True
        return self

    def __exit__(self, *exc):
        type(self).closed += 1
        type(self).open_now = False


@pytest.fixture(autouse=True)
def reset_spy():
    SpySession.opened = SpySession.closed = 0
    SpySession.open_now = False
    yield


@pytest.fixture
def qt_settings(project_paths):
    from src.core.enums import Mode
    from src.core.settings import load_settings

    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.TEST
    s.publishing.upload_method = "hosted_url"
    s.publishing.hosted_url_provider = "cloudflare_quick_tunnel"
    return s


def make_job(db, settings, key="qt-1"):
    from src.core.enums import ContentStatus, JobStatus

    cid, _ = db.insert_content(dict(
        content_type="philosophical_thought", text="Un pensiero.",
        normalized_text=f"un pensiero {key}", content_hash=f"c-{key}",
        caption="Una spiegazione utile.", hashtags=["#a"], mood="calm",
        quality_score=0.9, status=ContentStatus.APPROVED_FOR_PUBLICATION))
    out = Path(settings.paths.stories) / f"{key}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x00" * 8192)
    jid, _ = db.create_job(page_id=PAGE_ID, content_id=cid, media_type="reel",
                           idempotency_key=key, scheduled_at="2099-01-01T08:00:00Z",
                           status=JobStatus.MEDIA_READY)
    db.update_job(jid, output_path=str(out), upload_method="hosted_url")
    return jid


def build_publisher(settings, db, monkeypatch, mock, *, armed=True):
    from src.accounts import load_pages
    from src.publishing import Publisher
    from src.publishing import quick_tunnel as qt_mod
    from src.publishing.arming import set_armed
    from src.publishing.publisher import PublishTarget

    monkeypatch.setattr(qt_mod, "TunnelSession", SpySession)
    registry = load_pages(settings.paths)
    if armed:
        for page in registry.all():
            set_armed(db, page.page_id, True)
    return Publisher(settings, db, registry,
                     client_factory=lambda p: PublishTarget(mock, "ig"),
                     sleep=lambda _s: None)


def published_calls(mock):
    return [c for c in mock.calls if c[0] == "publish"]


# ---------------------------------------------------------------------------
def test_the_whole_publication_happens_inside_one_tunnel(tmp_db, qt_settings,
                                                         monkeypatch):
    from src.core.enums import JobStatus
    from src.publishing import MockGraphClient

    mock = MockGraphClient()
    jid = make_job(tmp_db, qt_settings)
    outcome = build_publisher(qt_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    assert outcome.status == JobStatus.PUBLISHED
    assert SpySession.opened == 1 and SpySession.closed == 1
    assert not SpySession.open_now, "nessun tunnel resta aperto a riposo"
    assert len(published_calls(mock)) == 1


def test_the_container_carries_the_tunnel_url_and_no_binary_upload(
        tmp_db, qt_settings, monkeypatch):
    from src.publishing import MockGraphClient

    mock = MockGraphClient()
    jid = make_job(tmp_db, qt_settings)
    build_publisher(qt_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    create = next(c for c in mock.calls if c[0] == "create")
    assert create[2] == "https://spy.trycloudflare.com/media/abc.mp4"
    assert not any(c[0] in ("create_resumable", "upload") for c in mock.calls)


def test_a_second_call_publishes_nothing_and_opens_no_tunnel(tmp_db, qt_settings,
                                                             monkeypatch):
    from src.publishing import MockGraphClient

    mock = MockGraphClient()
    jid = make_job(tmp_db, qt_settings)
    publisher = build_publisher(qt_settings, tmp_db, monkeypatch, mock)
    publisher.publish_job(jid)
    publisher.publish_job(jid)

    assert len(published_calls(mock)) == 1, "mai due media_publish per lo stesso job"
    assert SpySession.opened == 1


def test_a_finished_container_from_a_crashed_run_publishes_without_a_tunnel(
        tmp_db, qt_settings, monkeypatch):
    """Meta already has the media; reopening a tunnel would prove nothing."""
    from src.publishing import MockGraphClient

    mock = MockGraphClient(status_sequence=["FINISHED"])
    jid = make_job(tmp_db, qt_settings, key="qt-finished")
    container = mock.create_media_container("ig", media_type="REELS",
                                            video_url="https://old.example/x.mp4")
    tmp_db.update_job(jid, container_id=container)

    outcome = build_publisher(qt_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    assert outcome.status == "PUBLISHED"
    assert SpySession.opened == 0, "nessun tunnel per un container già FINISHED"
    assert len(published_calls(mock)) == 1


def test_a_stale_container_is_discarded_rather_than_waited_on(tmp_db, qt_settings,
                                                              monkeypatch):
    """Its video_url pointed at a tunnel that is gone: it can never finish."""
    from src.publishing import MockGraphClient

    mock = MockGraphClient()
    jid = make_job(tmp_db, qt_settings, key="qt-stale")
    tmp_db.update_job(jid, container_id="container-di-un-tunnel-morto")

    outcome = build_publisher(qt_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    assert outcome.status == "PUBLISHED"
    assert SpySession.opened == 1, "serve un tunnel nuovo, non attendere il vecchio"
    assert tmp_db.get_job(jid)["container_id"] != "container-di-un-tunnel-morto"


def test_the_tunnel_is_closed_even_when_meta_fails(tmp_db, qt_settings, monkeypatch):
    from src.publishing import MockGraphClient

    mock = MockGraphClient(container_error=True)
    jid = make_job(tmp_db, qt_settings, key="qt-error")
    outcome = build_publisher(qt_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    assert outcome.status != "PUBLISHED"
    assert SpySession.opened == 1 and SpySession.closed == 1
    assert not SpySession.open_now, "il cleanup avviene anche in errore"


def test_an_unarmed_page_never_reaches_the_tunnel(tmp_db, qt_settings, monkeypatch):
    """The arming switch still comes first, before any process is started."""
    from src.core.enums import JobStatus
    from src.publishing import MockGraphClient

    mock = MockGraphClient()
    jid = make_job(tmp_db, qt_settings, key="qt-unarmed")
    outcome = build_publisher(qt_settings, tmp_db, monkeypatch, mock,
                              armed=False).publish_job(jid)

    assert outcome.status == JobStatus.NEEDS_REVIEW
    assert SpySession.opened == 0
    assert not mock.calls


def test_dry_run_opens_no_tunnel_at_all(tmp_db, qt_settings, monkeypatch):
    from src.core.enums import Mode
    from src.publishing import MockGraphClient

    qt_settings.mode = Mode.DRY_RUN
    mock = MockGraphClient()
    jid = make_job(tmp_db, qt_settings, key="qt-dry")
    outcome = build_publisher(qt_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    assert str(outcome.remote_media_id or "").startswith("DRYRUN-")
    assert SpySession.opened == 0
    assert not mock.calls


def test_only_the_named_page_is_touched(tmp_db, qt_settings, monkeypatch):
    from src.publishing import MockGraphClient

    mock = MockGraphClient()
    jid = make_job(tmp_db, qt_settings, key="qt-single")
    build_publisher(qt_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    rows = tmp_db.list_jobs()
    assert {r["page_id"] for r in rows} == {PAGE_ID}
    assert len([r for r in rows if r["status"] == "PUBLISHED"]) == 1


def test_the_provider_is_read_from_the_page_then_the_global_setting(qt_settings,
                                                                    tmp_db):
    from src.accounts import load_pages
    from src.publishing import Publisher

    registry = load_pages(qt_settings.paths)
    publisher = Publisher(qt_settings, tmp_db, registry)
    page = registry.get(PAGE_ID)
    assert publisher.hosted_url_provider(page) == "cloudflare_quick_tunnel"

    page.publishing.hosted_url_provider = "public_base_url"
    assert publisher.hosted_url_provider(page) == "public_base_url"


def test_the_page_is_configured_for_the_tunnel_in_the_repository(project_paths):
    """The configuration that publishes must be the one that was validated."""
    from src.accounts import load_pages

    page = load_pages(project_paths).get(PAGE_ID)
    assert page.instagram.api_flavor == "facebook_login"
    assert page.publishing.upload_method == "hosted_url"
    assert page.publishing.hosted_url_provider == "cloudflare_quick_tunnel"


def test_the_other_four_pages_are_not_migrated_with_it(project_paths):
    from src.accounts import load_pages

    for page in load_pages(project_paths).all():
        if page.page_id == PAGE_ID:
            continue
        assert page.publishing.hosted_url_provider != "cloudflare_quick_tunnel", (
            f"{page.page_id} non è stata validata per il tunnel")
