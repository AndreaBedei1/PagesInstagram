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
    # 5 pages × 1 main job/day (stories are OFF by default) = 5 jobs/day
    assert r1.created == 5
    r2 = plan_jobs(tmp_db, reg, s, days=1)
    assert r2.created == 0
    assert r2.existing == 5


def test_planner_local_time_is_correct(tmp_db, project_paths):
    reg = load_pages(project_paths)
    s = load_settings(project_paths, load_dotenv=False)
    plan_jobs(tmp_db, reg, s, days=1)
    jobs = tmp_db.list_jobs(page_id="pensiero_essenziale_it")
    # main content is a 4:5 image post at the page's feed_time slot
    post = next(j for j in jobs if j["media_type"] == "feed_image")
    local = parse_iso(post["scheduled_at"]).astimezone(ZoneInfo("Europe/Rome"))
    assert (local.hour, local.minute) == (8, 30)  # DST-safe
    # no Story is planned with the default configuration
    assert {j["media_type"] for j in jobs} == {"feed_image"}


def test_missed_policy_within_window(tmp_db, project_paths):
    reg = load_pages(project_paths)
    s = load_settings(project_paths, load_dotenv=False)
    w = Worker(s, tmp_db, reg)
    now = now_utc()

    def iso(dt):
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # every evergreen page: publish_within_window (240 min)
    pid = "pensiero_essenziale_it"
    future = {"page_id": pid, "scheduled_at": iso(now + timedelta(hours=3))}
    near = {"page_id": pid, "scheduled_at": iso(now - timedelta(minutes=60))}
    old = {"page_id": pid, "scheduled_at": iso(now - timedelta(hours=6))}
    assert w._missed_action(future, now) == "wait"
    assert w._missed_action(near, now) == "publish"
    assert w._missed_action(old, now) == "skip"
