"""Regression tests: daily content/music sharing, music disable, ComfyUI
fallback policy in production."""
from __future__ import annotations

from datetime import timedelta

from src.accounts.models import PageConfig
from src.accounts.registry import AccountRegistry
from src.core.enums import ContentStatus, Mode
from src.core.settings import load_settings
from src.core.timeutils import now_utc
from src.database import Database
from src.scheduling.pipeline import DailyMedia, GenerationPipeline
from src.scheduling.planner import plan_jobs
from src.scheduling.worker import TickStats, Worker


def _page(**pub):
    p = {"publish_feed": True, "publish_story": True, "feed_time": "12:00",
         "story_time": "18:00", "missed_job_policy": "publish_immediately"}
    p.update(pub)
    return PageConfig(page_id="motivational_it", display_name="M",
                      content_type="motivational", publishing=p,
                      visual={"background_profile": "neutral_soft"})


class _FakePipeline:
    def __init__(self):
        self.calls = []

    def generate_daily(self, page, content, *, music_track_id=None, try_comfyui=True,
                       scheduled_date=None, cycle_number=0, generation_round=0):
        self.calls.append((page.page_id, content["id"], music_track_id,
                           generation_round))
        return DailyMedia(content_id=content["id"], ok=True,
                          video_path=f"/fake/{page.page_id}_{content['id']}.mp4",
                          image_path="/fake/i.png", background_path="/fake/b.png",
                          music_track_id="tone_calm_1", validation_score=0.9,
                          media_asset_id=None)


def _past(minutes=5):
    return (now_utc() - timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def test_reel_and_story_share_content_music_and_video(tmp_path, project_paths):
    """The five live pages publish stills now, so this page asks for REELS.

    The sharing rule — one content, one render, one music track for both
    formats of the day — belongs to the video path, which still exists for any
    page configured for it. Testing it needs a page that wants a Reel.
    """
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.DRY_RUN
    db = Database.open(tmp_path / "daily.sqlite")
    db.insert_content(dict(content_type="motivational", text="Un passo alla volta.",
                           normalized_text="x", content_hash="daily1", mood="calm",
                           caption="c", quality_score=0.95,
                           status=ContentStatus.APPROVED_FOR_PUBLICATION))
    reg = AccountRegistry({"motivational_it": _page(feed_media_type="REELS")})
    plan_jobs(db, reg, s, days=1)
    for j in db.list_jobs(page_id="motivational_it"):
        db.update_job(j["id"], scheduled_at=_past())

    worker = Worker(s, db, reg, pipeline=_FakePipeline(),
                    prepare_ahead_minutes=24 * 60, try_comfyui=False)
    worker._prepare_media(TickStats())

    jobs = db.list_jobs(page_id="motivational_it")
    reel = next(j for j in jobs if j["media_type"] == "reel")
    story = next(j for j in jobs if j["media_type"] == "story_video")
    # SAME content, SAME video for both formats of the day
    assert reel["content_id"] == story["content_id"]
    assert reel["output_path"] == story["output_path"]
    assert reel["status"] == "MEDIA_READY" and story["status"] == "MEDIA_READY"
    # generated exactly once for the day (not once per format)
    assert len(worker.pipeline.calls) == 1
    # the shared daily assignment records the same music track
    daily_row = db.conn.execute("SELECT * FROM daily_content LIMIT 1").fetchone()
    assert daily_row["music_track_id"] == "tone_calm_1"
    assert daily_row["content_id"] == reel["content_id"]
    db.close()


def test_music_disabled_produces_no_track(tmp_db, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    pipe = GenerationPipeline(s, tmp_db)
    page = _page()
    page.music.enabled = False
    tid, path = pipe._resolve_music(page, {"mood": "calm"}, None)
    assert tid is None and path is None


def test_music_reused_by_track_id(tmp_db, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    tmp_db.upsert_track(dict(track_id="tone_calm_1", title="t", file_path="/x/t.wav",
                             license="CC0-1.0", category="calm", mood="calm",
                             instagram_safe=1))
    pipe = GenerationPipeline(s, tmp_db)
    tid, path = pipe._resolve_music(_page(), {"mood": "calm"}, "tone_calm_1")
    assert tid == "tone_calm_1" and path == "/x/t.wav"


def test_comfyui_fallback_forbidden_in_production(tmp_db, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.PRODUCTION
    s.comfyui.url = "http://127.0.0.1:1"          # unreachable
    s.comfyui.allow_fallback_in_production = False
    pipe = GenerationPipeline(s, tmp_db)
    content = {"id": 1, "text": "x", "mood": "calm", "background_prompt": "p"}
    res = pipe.generate_daily(_page(), content, try_comfyui=True)
    assert res.ok is False
    assert "fallback" in res.message.lower()
