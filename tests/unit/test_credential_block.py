"""The two days of silence, and what now prevents them.

On 14 and 15 August the worker woke on time, opened a tunnel and asked Meta to
publish. Meta answered `[200] Cannot call API for app … on behalf of user …`
because API access for the app had been blocked. The engine marked the job
FAILED and burned the next one the following morning: two posts lost, and
nothing anywhere said why.

These tests hold both halves of the fix. A credentials refusal must not consume
the day's job — the media is fine, only the permission is missing — and it must
stop the attempts until the credentials work again, instead of repeating daily.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.enums import ContentStatus, JobStatus, MediaType, Mode
from src.core.errors import PublishError
from src.core.settings import load_settings
from src.publishing import MockGraphClient, Publisher, credential_block
from src.core.timeutils import now_utc
from src.publishing.publisher import PublishTarget

PAGE_ID = "pensiero_essenziale_it"


def _due_now() -> str:
    """A slot five minutes past: due, and well inside the missed-job window."""
    return (now_utc() - timedelta(minutes=5)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")

#: The exact message Meta sent, twice, on two consecutive mornings.
REAL_MESSAGE = ("Graph API error [200]: Cannot call API for app "
                "27892488463733883 on behalf of user 122104922523428652")


# ---- recognising the thing -------------------------------------------------
def test_the_real_refusals_are_recognised():
    assert credential_block.is_auth_error(
        PublishError(REAL_MESSAGE, retryable=False, code="200"))
    assert credential_block.is_auth_error(
        PublishError("Graph API error [200]: API access blocked.",
                     retryable=False, code="200"))
    assert credential_block.is_auth_error(
        PublishError("Graph API error [190]: Error validating access token",
                     retryable=False, code="190"))


def test_a_real_job_failure_is_not_mistaken_for_one():
    """Media, schedule and tunnel problems must keep failing per job."""
    for e in (PublishError("file media mancante", retryable=False, code="FILE_MISSING"),
              PublishError("container ERROR", retryable=False, code="ERROR"),
              PublishError("il tunnel non ha superato i controlli",
                           retryable=True, code="TUNNEL_HEALTH"),
              PublishError("Graph API error [4]: rate limit", retryable=True, code="4")):
        assert not credential_block.is_auth_error(e), e


# ---- the state --------------------------------------------------------------
def test_the_first_refusal_is_the_one_that_dates_the_block(tmp_db):
    first = credential_block.record_block(tmp_db, PAGE_ID, REAL_MESSAGE)
    second = credential_block.record_block(tmp_db, PAGE_ID, REAL_MESSAGE)
    assert second["since"] == first["since"], (
        "'bloccata dal 14' è azionabile, 'bloccata da un minuto' no")
    assert second["attempts"] == 2
    credential_block.clear_block(tmp_db, PAGE_ID)
    assert credential_block.block_state(tmp_db, PAGE_ID) is None


def test_the_probe_waits_before_asking_again():
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    state = {"since": "2026-08-14T06:31:42Z",
             "last_probe": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert not credential_block.due_for_probe(state, now=now)
    state["last_probe"] = (now - timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert credential_block.due_for_probe(state, now=now)
    assert not credential_block.due_for_probe(None, now=now)


# ---- the publisher ----------------------------------------------------------
def _settings(project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.PRODUCTION
    s.publishing.upload_method = "hosted_url"
    s.publishing.hosted_url_provider = "cloudflare_quick_tunnel"
    return s


def _job(db, settings, key="cred"):
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


class RefusingClient(MockGraphClient):
    """Meta as it actually behaved on the 14th."""

    def create_media_container(self, *a, **k):
        self.calls.append(("create", a, k))
        raise PublishError(REAL_MESSAGE, retryable=False, code="200")


class SpySession:
    def __init__(self, media_path, paths, **kw):
        self.public_url = "https://spy.trycloudflare.com/media/abc.png"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _publisher(settings, db, monkeypatch, client):
    from src.accounts import load_pages
    from src.publishing import quick_tunnel as qt_mod
    from src.publishing.arming import set_armed

    monkeypatch.setattr(qt_mod, "TunnelSession", SpySession)
    registry = load_pages(settings.paths)
    set_armed(db, PAGE_ID, True)
    return Publisher(settings, db, registry,
                     client_factory=lambda p: PublishTarget(client, "ig"),
                     sleep=lambda _s: None)


def test_a_refused_publication_does_not_consume_the_day(tmp_db, project_paths,
                                                        monkeypatch):
    settings = _settings(project_paths)
    jid = _job(tmp_db, settings, "cred-keep")
    outcome = _publisher(settings, tmp_db, monkeypatch,
                         RefusingClient()).publish_job(jid)

    job = tmp_db.get_job(jid)
    assert job["status"] == JobStatus.MEDIA_READY, (
        "il post del giorno deve restare pubblicabile: manca il permesso, "
        "non il media")
    assert job["status"] != JobStatus.FAILED
    assert "200" in (job["last_error"] or "")
    assert outcome.status == JobStatus.MEDIA_READY
    assert credential_block.block_state(tmp_db, PAGE_ID)


def test_a_media_failure_still_fails_the_job(tmp_db, project_paths, monkeypatch):
    """The new path must not swallow the failures that really are the job's."""
    settings = _settings(project_paths)
    jid = _job(tmp_db, settings, "cred-media")
    tmp_db.update_job(jid, output_path=str(Path(settings.paths.posts) / "assente.png"))
    outcome = _publisher(settings, tmp_db, monkeypatch,
                         MockGraphClient()).publish_job(jid)
    assert outcome.status == JobStatus.FAILED
    assert credential_block.block_state(tmp_db, PAGE_ID) is None


# ---- the worker -------------------------------------------------------------
def _worker(settings, db, monkeypatch, client):
    from src.accounts import load_pages
    from src.scheduling.worker import Worker

    publisher = _publisher(settings, db, monkeypatch, client)
    return Worker(settings, db, load_pages(settings.paths), publisher=publisher,
                  try_comfyui=False, plan_enabled=False, cleanup_enabled=False)


def test_the_worker_stops_asking_instead_of_burning_a_post_a_day(
        tmp_db, project_paths, monkeypatch):
    settings = _settings(project_paths)
    jid = _job(tmp_db, settings, "cred-worker")
    tmp_db.update_job(jid, scheduled_at=_due_now())
    client = RefusingClient()
    worker = _worker(settings, tmp_db, monkeypatch, client)

    first = worker.run_once()
    assert first.published == 0
    calls_after_first = len(client.calls)
    assert calls_after_first >= 1, "il primo tentativo va fatto davvero"

    second = worker.run_once()
    assert second.blocked >= 1, "il secondo tick non deve nemmeno provarci"
    assert len(client.calls) == calls_after_first, (
        "nessuna nuova chiamata a Meta finché il blocco è recente")
    assert tmp_db.get_job(jid)["status"] == JobStatus.MEDIA_READY


def test_it_resumes_by_itself_when_meta_accepts_again(tmp_db, project_paths,
                                                      monkeypatch):
    """No operator, no restart: the block clears on the first good probe."""
    settings = _settings(project_paths)
    jid = _job(tmp_db, settings, "cred-resume")
    tmp_db.update_job(jid, scheduled_at=_due_now())
    client = RefusingClient()
    worker = _worker(settings, tmp_db, monkeypatch, client)
    worker.run_once()
    assert credential_block.block_state(tmp_db, PAGE_ID)

    # Meta starts answering again, and enough time has passed to ask.
    monkeypatch.setattr(credential_block, "due_for_probe",
                        lambda *a, **k: True)
    worker.publisher._factory = lambda page: PublishTarget(MockGraphClient(), "ig")
    stats = worker.run_once()
    assert credential_block.block_state(tmp_db, PAGE_ID) is None
    assert stats.published == 1, "il post del giorno esce, non è stato perso"
    assert tmp_db.get_job(jid)["status"] == JobStatus.PUBLISHED


def test_an_inconclusive_probe_does_not_unblock(tmp_db):
    """A network hiccup is not Meta saying yes."""

    class Flaky:
        def get_account_info(self, _uid):
            raise PublishError("network error: timeout", retryable=True)

    ok, detail = credential_block.probe(PublishTarget(Flaky(), "ig"))
    assert ok is True and "non conclusivo" in detail
