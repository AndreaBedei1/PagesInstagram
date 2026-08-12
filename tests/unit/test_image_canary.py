"""The canary for a page that publishes a still through a Quick Tunnel.

The resumable canary is two commands — upload, then publish — because the bytes
sit on Meta in between. A tunnel publication cannot be split that way: the
container is created from a URL that exists only while the tunnel is open, so
"upload now, publish later" would hand Meta an address that stops resolving.

So the shape changes and the guarantees must not. These tests exist to hold the
second half of that sentence: same word, same terminal, same audit, same health
check, same once-and-only-once, same disarmed page afterwards.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.core.enums import ContentStatus, JobStatus, MediaType, Mode
from src.core.settings import load_settings
from src.publishing import MockGraphClient, Publisher
from src.publishing.publisher import PublishTarget

ROOT = Path(__file__).resolve().parents[2]
PAGE_ID = "pensiero_essenziale_it"


@pytest.fixture
def settings(project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.TEST
    s.publishing.upload_method = "hosted_url"
    s.publishing.hosted_url_provider = "cloudflare_quick_tunnel"
    return s


@pytest.fixture
def page(project_paths):
    from src.accounts import load_pages

    p = load_pages(project_paths).get(PAGE_ID)
    p.publishing.upload_method = "hosted_url"
    p.publishing.hosted_url_provider = "cloudflare_quick_tunnel"
    return p


class SpySession:
    """Stands in for the tunnel: no cloudflared, no socket, no port."""

    opened = 0

    def __init__(self, media_path, paths, **kw):
        self.public_url = "https://spy.trycloudflare.com/media/abc.png"

    def __enter__(self):
        type(self).opened += 1
        return self

    def __exit__(self, *exc):
        return False


def _image_job(db, settings, key="canary-img"):
    cid, _ = db.insert_content(dict(
        content_type="philosophical_thought", text="Un pensiero.",
        normalized_text=f"un pensiero {key}", content_hash=f"c-{key}",
        caption="Una spiegazione.", hashtags=["#a"], mood="calm",
        quality_score=0.95, status=ContentStatus.APPROVED_FOR_PUBLICATION))
    out = Path(settings.paths.posts) / f"{key}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048)
    jid, _ = db.create_job(page_id=PAGE_ID, content_id=cid,
                           media_type=MediaType.FEED_IMAGE, idempotency_key=key,
                           scheduled_at="2099-01-01T08:00:00Z",
                           status=JobStatus.MEDIA_READY)
    db.update_job(jid, output_path=str(out))
    return jid


def _publisher(settings, db, monkeypatch, mock):
    from src.accounts import load_pages
    from src.publishing import quick_tunnel as qt_mod

    monkeypatch.setattr(qt_mod, "TunnelSession", SpySession)
    return Publisher(settings, db, load_pages(settings.paths),
                     client_factory=lambda p: PublishTarget(mock, "ig"),
                     sleep=lambda _s: None)


def _publish(db, settings, page, monkeypatch, mock, **overrides):
    from src.publishing import canary

    kwargs = dict(confirmation=canary.CONFIRMATION, interactive=True,
                  audit_ok=True, health_ok=True)
    job_id = overrides.pop("job_id")
    kwargs.update(overrides)
    return canary.publish_canary_hosted(
        db, publisher=_publisher(settings, db, monkeypatch, mock), page=page,
        job_id=job_id, target=PublishTarget(mock, "ig"), **kwargs)


# ---- the gate, without any I/O at all -------------------------------------
def _gate(db, job_id=None, **overrides):
    from src.publishing import canary

    kwargs = dict(page_id=PAGE_ID, job_id=job_id,
                  confirmation=canary.CONFIRMATION, interactive=True,
                  container_status="", audit_ok=True, health_ok=True,
                  transport="hosted_url")
    kwargs.update({k: v for k, v in overrides.items() if v is not None})
    return canary.check_publish_allowed(db, **kwargs)


def test_the_tunnel_canary_needs_no_pre_existing_container(tmp_db, settings):
    """The condition the resumable canary demands is impossible here."""
    jid = _image_job(tmp_db, settings, "gate-ok")
    ok, reason = _gate(tmp_db, jid)
    assert ok, reason


def test_a_left_over_container_stops_the_tunnel_canary(tmp_db, settings):
    """Its URL is dead, so it cannot be reused — and it means something ran."""
    from src.publishing import canary

    jid = _image_job(tmp_db, settings, "gate-leftover")
    canary.save_state(tmp_db, canary.CanaryState(page_id=PAGE_ID, job_id=jid,
                                                 container_id="18000"))
    ok, reason = _gate(tmp_db, jid)
    assert not ok and "18000" in reason


def test_every_other_condition_is_unchanged(tmp_db, settings):
    jid = _image_job(tmp_db, settings, "gate-rest")
    assert not _gate(tmp_db, jid, confirmation="pubblica")[0]
    assert not _gate(tmp_db, jid, confirmation="")[0]
    assert not _gate(tmp_db, jid, interactive=False)[0]
    assert not _gate(tmp_db, jid, audit_ok=False)[0]
    assert not _gate(tmp_db, jid, health_ok=False)[0]
    assert not _gate(tmp_db, jid + 5000)[0]


# ---- the publication ------------------------------------------------------
def test_it_publishes_one_image_once(tmp_db, settings, page, monkeypatch):
    mock = MockGraphClient()
    SpySession.opened = 0
    jid = _image_job(tmp_db, settings, "pub-once")

    outcome = _publish(tmp_db, settings, page, monkeypatch, mock, job_id=jid)
    assert outcome.published, outcome.reason
    assert outcome.media_id
    create = next(c for c in mock.calls if c[0] == "create")
    assert create[1] == "IMAGE"
    assert SpySession.opened == 1
    assert len([c for c in mock.calls if c[0] == "publish"]) == 1

    second = _publish(tmp_db, settings, page, monkeypatch, mock, job_id=jid)
    assert not second.published
    assert len([c for c in mock.calls if c[0] == "publish"]) == 1, (
        "il canary non deve poter pubblicare un secondo media")


def test_it_does_not_arm_the_page(tmp_db, settings, page, monkeypatch):
    from src.publishing.arming import is_armed

    mock = MockGraphClient()
    jid = _image_job(tmp_db, settings, "pub-noarm")
    outcome = _publish(tmp_db, settings, page, monkeypatch, mock, job_id=jid)
    assert outcome.published
    assert not is_armed(tmp_db, PAGE_ID), "il canary non arma nulla"


def test_a_refusal_opens_no_tunnel_at_all(tmp_db, settings, page, monkeypatch):
    """The word is checked before anything is opened, not after."""
    mock = MockGraphClient()
    SpySession.opened = 0
    jid = _image_job(tmp_db, settings, "pub-refused")
    outcome = _publish(tmp_db, settings, page, monkeypatch, mock, job_id=jid,
                       confirmation="ok")
    assert not outcome.published
    assert SpySession.opened == 0 and not mock.calls


def test_the_attempt_is_recorded_before_the_call(tmp_db, settings, page,
                                                 monkeypatch):
    """A crash mid-publication must not look like a publication that never was."""
    from src.publishing import canary

    class Exploding(MockGraphClient):
        def create_media_container(self, *a, **k):
            raise RuntimeError("la rete è caduta a metà")

    mock = Exploding()
    jid = _image_job(tmp_db, settings, "pub-crash")
    with pytest.raises(RuntimeError):
        _publish(tmp_db, settings, page, monkeypatch, mock, job_id=jid)

    assert canary.attempt_record(tmp_db, PAGE_ID, "feed_image"), (
        "il tentativo deve restare registrato")
    assert canary.published_record(tmp_db, PAGE_ID, "feed_image") is None

    # and the marker is what stops a blind retry on another job
    other = _image_job(tmp_db, settings, "pub-crash-2")
    ok, reason = _gate(tmp_db, other)
    assert not ok and "tentativo" in reason.lower()


# ---- the escape hatch that must not become one ----------------------------
def test_only_the_canary_may_publish_an_unarmed_page():
    """`publish_canary_media` skips the arming gate. One caller, checked here."""
    callers = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if path.name in ("canary.py", "publisher.py"):
            continue
        if re.search(r"\bpublish_canary_media\b", path.read_text(encoding="utf-8")):
            callers.append(str(path.relative_to(ROOT)))
    assert not callers, (
        f"solo canary.py può pubblicare senza armamento; lo chiamano anche: "
        f"{callers}")


def test_the_worker_still_cannot_publish_an_unarmed_page(tmp_db, settings,
                                                         monkeypatch):
    """The new method exists; the ordinary path is unchanged by it."""
    mock = MockGraphClient()
    jid = _image_job(tmp_db, settings, "worker-unarmed")
    outcome = _publisher(settings, tmp_db, monkeypatch, mock).publish_job(jid)
    assert outcome.status == JobStatus.NEEDS_REVIEW
    assert not mock.calls


def test_once_means_once_per_page_and_per_format(tmp_db, settings, page,
                                                 monkeypatch):
    """The Reel canary of this page must not block its image canary.

    The marker used to be global — one row for the whole project — which read
    as "every page has already had its canary" and would have made the other
    four accounts impossible to start.
    """
    from src.core.enums import JobStatus
    from src.publishing import canary

    mock = MockGraphClient()
    jid = _image_job(tmp_db, settings, "scope-img")

    # a Reel canary already recorded for this same page, the legacy way
    reel_job, _ = tmp_db.create_job(page_id=PAGE_ID, content_id=None,
                                    media_type=MediaType.REEL,
                                    idempotency_key="scope-reel",
                                    scheduled_at="2026-01-01T08:00:00Z",
                                    status=JobStatus.PUBLISHED)
    tmp_db.update_job(reel_job, remote_media_id="17999")
    canary._write(tmp_db, canary._USED_KEY,
                  {"page_id": PAGE_ID, "job_id": reel_job, "media_id": "17999",
                   "published_at": "2026-08-12T07:40:19Z"})

    assert canary.published_record(tmp_db, PAGE_ID, "reel"), "il Reel è uscito"
    assert canary.published_record(tmp_db, PAGE_ID, "feed_image") is None
    outcome = _publish(tmp_db, settings, page, monkeypatch, mock, job_id=jid)
    assert outcome.published, outcome.reason


def test_a_finished_container_from_a_past_canary_is_not_an_obstacle(
        tmp_db, settings, page, monkeypatch):
    """It is history: its job is published and its tunnel closed long ago."""
    from src.core.enums import JobStatus
    from src.publishing import canary

    mock = MockGraphClient()
    old_job, _ = tmp_db.create_job(page_id=PAGE_ID, content_id=None,
                                   media_type=MediaType.REEL,
                                   idempotency_key="old-reel",
                                   scheduled_at="2026-01-01T08:00:00Z",
                                   status=JobStatus.PUBLISHED)
    tmp_db.update_job(old_job, remote_media_id="18000")
    canary.save_state(tmp_db, canary.CanaryState(page_id=PAGE_ID, job_id=old_job,
                                                 container_id="18085644884273783"))
    jid = _image_job(tmp_db, settings, "after-old")
    assert _gate(tmp_db, jid)[0], _gate(tmp_db, jid)[1]

    # but one left mid-flight still stops everything
    stuck, _ = tmp_db.create_job(page_id=PAGE_ID, content_id=None,
                                 media_type=MediaType.FEED_IMAGE,
                                 idempotency_key="stuck",
                                 scheduled_at="2099-01-01T08:00:00Z",
                                 status=JobStatus.CONTAINER_CREATED)
    canary.save_state(tmp_db, canary.CanaryState(page_id=PAGE_ID, job_id=stuck,
                                                 container_id="18999"))
    ok, reason = _gate(tmp_db, jid)
    assert not ok and "18999" in reason
