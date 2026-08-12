"""Worker-level requirements for the five evergreen pages.

Covers: the rolling generation buffer, published-media retention, per-page/date
idempotency, mock publication for every page, the single-instance lock, and the
dry-run job counts (5 jobs for one day, 35 for seven days, zero Stories).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.accounts import load_pages
from src.content.dataset_validation import ALL_CALENDAR_KEYS
from src.core.enums import ContentStatus, JobStatus, Mode
from src.core.settings import load_settings
from src.core.timeutils import now_utc, utcnow_iso
from src.database import Database
from src.scheduling.pipeline import DailyMedia
from src.scheduling.planner import plan_jobs
from src.scheduling.worker import TickStats, Worker

pytestmark = pytest.mark.integration


class FakePipeline:
    """Records calls and returns a ready-made video path (no ffmpeg, no GPU)."""

    def __init__(self, out_dir):
        self.calls: list[tuple] = []
        self.out_dir = out_dir

    def generate_daily(self, page, content, *, music_track_id=None,
                       try_comfyui=True, scheduled_date=None, cycle_number=0,
                       generation_round=0):
        self.calls.append((page.page_id, content["id"], scheduled_date, cycle_number,
                           generation_round))
        path = self.out_dir / f"{page.page_id}_{scheduled_date}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 2048)
        return DailyMedia(content_id=content["id"], ok=True, video_path=str(path),
                          image_path=None, background_path=None,
                          music_track_id=None, validation_score=0.95,
                          media_asset_id=None)


def _seed_all(db: Database) -> None:
    for ctype in ("philosophical_thought", "world_curiosity",
                  "word_of_the_day", "daily_question"):
        for i in range(400):
            db.insert_content(dict(
                content_type=ctype, text=f"{ctype} numero {i} di prova.",
                normalized_text=f"{ctype} {i}", content_hash=f"{ctype}-{i}",
                sequence_index=i, quality_score=0.95, caption="Didascalia di prova.",
                status=ContentStatus.APPROVED_FOR_PUBLICATION))
    idx = 0
    for key in ALL_CALENDAR_KEYS:
        for k in range(2):
            db.insert_content(dict(
                content_type="today_in_history", text=f"Evento {key} numero {k}",
                normalized_text=f"evento {key} {k}", content_hash=f"th-{key}-{k}",
                calendar_key=key, sequence_index=idx, quality_score=0.9,
                caption="Didascalia storica.",
                metadata_json={"year": str(1900 + k), "description": "d"},
                status=ContentStatus.APPROVED_FOR_PUBLICATION))
            idx += 1


@pytest.fixture
def env(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.DRY_RUN
    reg = load_pages(project_paths)
    db = Database.open(tmp_path / "worker.sqlite")
    _seed_all(db)
    yield s, reg, db, tmp_path
    db.close()


# ---- 27/28/29: dry-run planning ----------------------------------------
def test_one_day_plan_creates_exactly_five_main_jobs(env):
    s, reg, db, _ = env
    rep = plan_jobs(db, reg, s, days=1)
    jobs = db.list_jobs()
    assert rep.created == 5 and len(jobs) == 5
    assert {j["page_id"] for j in jobs} == set(reg.ids())


def test_seven_day_plan_creates_exactly_35_main_jobs(env):
    s, reg, db, _ = env
    plan_jobs(db, reg, s, days=7)
    assert len(db.list_jobs()) == 35


def test_no_story_is_planned_with_the_default_configuration(env):
    s, reg, db, _ = env
    plan_jobs(db, reg, s, days=7)
    assert {j["media_type"] for j in db.list_jobs()} == {"feed_image"}


def test_planning_is_idempotent(env):
    s, reg, db, _ = env
    plan_jobs(db, reg, s, days=3)
    second = plan_jobs(db, reg, s, days=3)
    assert second.created == 0 and second.existing == 15


# ---- 24: rolling buffer -------------------------------------------------
def test_worker_plans_60_days_and_prepares_only_30(env):
    s, reg, db, tmp_path = env
    pipeline = FakePipeline(tmp_path / "media")
    worker = Worker(s, db, reg, pipeline=pipeline, cleanup_enabled=False)
    stats = TickStats()
    worker._plan(stats)
    worker._prepare_media(stats)

    assert stats.planned == 5 * 60                      # planning horizon
    prepared = db.list_jobs(status=JobStatus.MEDIA_READY)
    # 30-day preparation window, inclusive of today -> at most 31 days per page
    per_page = len(prepared) / 5
    assert 30 <= per_page <= 31
    assert len(prepared) < stats.planned                # the rest stays scheduled
    assert all(j["output_path"] for j in prepared)


def test_prepared_media_uses_the_scheduled_date_and_cycle(env):
    s, reg, db, tmp_path = env
    pipeline = FakePipeline(tmp_path / "media")
    worker = Worker(s, db, reg, pipeline=pipeline, cleanup_enabled=False)
    worker.run_once()
    dates = {c[2] for c in pipeline.calls}
    assert all(len(d) == 10 and d[4] == "-" for d in dates)
    assert all(c[3] >= 0 for c in pipeline.calls)


# ---- 20: one content per page per day, stable across ticks -------------
def test_same_page_and_date_always_resolve_to_the_same_content(env):
    s, reg, db, tmp_path = env
    worker = Worker(s, db, reg, pipeline=FakePipeline(tmp_path / "m"),
                    cleanup_enabled=False)
    worker.run_once()
    first = {(j["page_id"], j["scheduled_at"][:10]): j["content_id"]
             for j in db.list_jobs()}
    worker.run_once()
    second = {(j["page_id"], j["scheduled_at"][:10]): j["content_id"]
              for j in db.list_jobs()}
    assert first == second
    assert all(v is not None for v in first.values() if v is not None)


# ---- 19: mock publication for each page --------------------------------
def test_dry_run_publishes_one_item_per_page_and_is_idempotent(env):
    s, reg, db, tmp_path = env
    worker = Worker(s, db, reg, pipeline=FakePipeline(tmp_path / "m"),
                    cleanup_enabled=False)
    plan_jobs(db, reg, s, days=1)
    worker._prepare_media(TickStats())
    for job in db.list_jobs(status=JobStatus.MEDIA_READY):
        db.update_job(job["id"], scheduled_at=utcnow_iso())

    stats = TickStats()
    worker._publish_due(stats)
    published = db.list_jobs(status=JobStatus.PUBLISHED)
    assert stats.published == 5 and len(published) == 5
    assert {j["page_id"] for j in published} == set(reg.ids())
    assert all(j["remote_media_id"].startswith("DRYRUN-") for j in published)

    again = TickStats()
    worker._publish_due(again)
    assert again.published == 0                       # never published twice


# ---- 25: retention of published media -----------------------------------
def test_old_published_media_is_deleted_but_reviewable_media_is_kept(env):
    s, reg, db, tmp_path = env
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    old = media / "old.mp4"
    kept = media / "review.mp4"
    old.write_bytes(b"x")
    kept.write_bytes(b"x")

    long_ago = (now_utc() - timedelta(days=100)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    page_id = "pensiero_essenziale_it"
    jid, _ = db.create_job(page_id=page_id, content_id=None, media_type="reel",
                           idempotency_key="old", scheduled_at=long_ago,
                           status=JobStatus.PUBLISHED)
    db.update_job(jid, output_path=str(old), published_at=long_ago)
    rid, _ = db.create_job(page_id=page_id, content_id=None, media_type="reel",
                           idempotency_key="review", scheduled_at=long_ago,
                           status=JobStatus.NEEDS_REVIEW)
    db.update_job(rid, output_path=str(kept))

    worker = Worker(s, db, reg, pipeline=FakePipeline(media))
    stats = TickStats()
    worker._cleanup_media(stats)

    assert stats.cleaned == 1
    assert not old.exists() and kept.exists()
    assert db.get_job(jid)["media_deleted_at"]
    # idempotent: a second pass finds nothing left to do
    stats2 = TickStats()
    worker._cleanup_media(stats2)
    assert stats2.cleaned == 0


def test_shared_media_is_kept_until_every_job_is_published(env):
    s, reg, db, tmp_path = env
    shared = tmp_path / "shared.mp4"
    shared.write_bytes(b"x")
    long_ago = (now_utc() - timedelta(days=100)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    a, _ = db.create_job(page_id="pensiero_essenziale_it", content_id=None,
                         media_type="reel", idempotency_key="a",
                         scheduled_at=long_ago, status=JobStatus.PUBLISHED)
    db.update_job(a, output_path=str(shared), published_at=long_ago)
    b, _ = db.create_job(page_id="pensiero_essenziale_it", content_id=None,
                         media_type="story_video", idempotency_key="b",
                         scheduled_at=long_ago, status=JobStatus.MEDIA_READY)
    db.update_job(b, output_path=str(shared))

    Worker(s, db, reg, pipeline=FakePipeline(tmp_path))._cleanup_media(TickStats())
    assert shared.exists()


# ---- 23: recovery after a crash ----------------------------------------
def test_worker_reports_inflight_jobs_after_restart(env):
    s, reg, db, tmp_path = env
    jid, _ = db.create_job(page_id="pensiero_essenziale_it", content_id=None,
                           media_type="reel", idempotency_key="inflight",
                           scheduled_at=utcnow_iso(),
                           status=JobStatus.CONTAINER_CREATED)
    worker = Worker(s, db, reg, pipeline=FakePipeline(tmp_path))
    assert worker.recover() == 1


# ---- 30: single worker instance ----------------------------------------
def test_only_one_worker_can_hold_the_lock(tmp_path):
    from src.scheduling.lock import SingleInstanceLock

    first = SingleInstanceLock(tmp_path / "worker.lock")
    second = SingleInstanceLock(tmp_path / "worker.lock")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()
