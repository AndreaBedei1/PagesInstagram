"""End-to-end dry-run: plan -> select content -> background(fallback) -> render ->
validate -> real video -> dry-run publish. Uses the deterministic fallback
background (no GPU) and a generated CC0 tone (no ffmpeg-less)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from src.accounts.models import PageConfig
from src.accounts.registry import AccountRegistry
from src.core.enums import ContentStatus, JobStatus, Mode
from src.core.settings import load_settings
from src.core.timeutils import now_utc
from src.database import Database
from src.music.library import generate_placeholder_library, sync_library
from src.scheduling.planner import plan_jobs
from src.scheduling.worker import Worker

pytestmark = pytest.mark.e2e


def test_full_dry_run_pipeline(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.DRY_RUN
    s.video.reel_duration_seconds = 2.0   # main content is a 9:16 Reel
    s.video.story_duration_seconds = 2.0
    s.video.ken_burns = False  # keep the test fast

    db = Database.open(tmp_path / "e2e.sqlite")

    # music: generate CC0 tones into a tmp dir and sync
    music_root = tmp_path / "music"
    music_root.mkdir()
    generate_placeholder_library(music_root, duration=3.0, per_mood=1)
    sync_library(db, music_root)

    # one approved content
    db.insert_content(dict(
        content_type="motivational", text="Un passo alla volta costruisci il tuo domani.",
        normalized_text="x", content_hash="e2e-1", mood="calm", category="costanza",
        caption="Ogni piccola azione ripetuta con costanza diventa un risultato concreto.",
        hashtags=["#motivazione"], background_prompt="soft warm gradient minimal",
        quality_score=0.95, status=ContentStatus.APPROVED_FOR_PUBLICATION))

    # single feed-only page (limits the test to one video)
    page = PageConfig(
        page_id="motivational_it", display_name="M", content_type="motivational",
        publishing={"publish_feed": True, "publish_story": False, "feed_time": "12:00",
                    "missed_job_policy": "publish_immediately"},
        visual={"show_author": False, "logo_enabled": True, "logo_text": "@m",
                "background_profile": "neutral_soft"},
    )
    reg = AccountRegistry({"motivational_it": page})

    plan_jobs(db, reg, s, days=1)
    job = db.list_jobs(page_id="motivational_it")[0]
    # force the slot into the recent past so it is due now
    past = (now_utc() - timedelta(minutes=5)).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")
    db.update_job(job["id"], scheduled_at=past)

    worker = Worker(s, db, reg, generate_ahead_days=1, prepare_ahead_minutes=24 * 60,
                    try_comfyui=False)
    stats = worker.run_once()

    final = db.get_job(job["id"])
    assert final["status"] == JobStatus.PUBLISHED, (final["status"], final["last_error"])
    assert final["remote_media_id"].startswith("DRYRUN")
    assert final["output_path"] and Path(final["output_path"]).exists()
    assert final["content_id"] is not None
    # media asset persisted with a validation score
    media = db.latest_media_for(final["content_id"], "motivational_it")
    assert media is not None
    assert media["post_video_path"] and Path(media["post_video_path"]).exists()
    db.close()
