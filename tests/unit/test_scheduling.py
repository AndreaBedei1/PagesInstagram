"""Tests for the daily planner and the worker's missed-job policy."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from src.accounts import load_pages
from src.core.settings import load_settings
from src.core.timeutils import now_utc, parse_iso
from src.scheduling.planner import plan_jobs
from src.scheduling.worker import Worker


def test_planner_creates_and_is_idempotent(tmp_db, project_paths):
    reg = load_pages(project_paths)
    s = load_settings(project_paths, load_dotenv=False)
    r1 = plan_jobs(tmp_db, reg, s, days=1)
    # 2 pages × (feed + story) = 4 jobs/day
    assert r1.created == 4
    r2 = plan_jobs(tmp_db, reg, s, days=1)
    assert r2.created == 0
    assert r2.existing == 4


def test_planner_local_time_is_correct(tmp_db, project_paths):
    reg = load_pages(project_paths)
    s = load_settings(project_paths, load_dotenv=False)
    plan_jobs(tmp_db, reg, s, days=1)
    jobs = tmp_db.list_jobs(page_id="motivational_it")
    # main content is now a REEL (shared to feed) at the feed_time slot
    reel = next(j for j in jobs if j["media_type"] == "reel")
    local = parse_iso(reel["scheduled_at"]).astimezone(ZoneInfo("Europe/Rome"))
    assert (local.hour, local.minute) == (12, 30)  # DST-safe
    assert {j["media_type"] for j in jobs} == {"reel", "story_video"}


def test_missed_policy_within_window(tmp_db, project_paths):
    reg = load_pages(project_paths)
    s = load_settings(project_paths, load_dotenv=False)
    w = Worker(s, tmp_db, reg)
    now = now_utc()

    def iso(dt):
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # motivational_it policy = publish_within_window (180 min)
    future = {"page_id": "motivational_it", "scheduled_at": iso(now + timedelta(hours=3))}
    near = {"page_id": "motivational_it", "scheduled_at": iso(now - timedelta(minutes=60))}
    old = {"page_id": "motivational_it", "scheduled_at": iso(now - timedelta(hours=6))}
    assert w._missed_action(future, now) == "wait"
    assert w._missed_action(near, now) == "publish"
    assert w._missed_action(old, now) == "skip"
