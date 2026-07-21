"""Persistent worker: plan jobs, generate media ahead of time, publish when due.

One tick = plan() -> prepare_media() -> publish_due(). ``run_forever`` loops with
a stop flag. Missed jobs (PC was off at the scheduled time) are handled per the
page's ``missed_job_policy``. A single-instance lock prevents two workers running.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

from ..accounts.registry import AccountRegistry
from ..core.enums import (ACTIVE_JOB_STATES, JobStatus, MediaType, MissedJobPolicy)
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from ..core.timeutils import now_utc, parse_iso, utcnow_iso
from ..database import Database
from ..content.service import select_content_for_page
from ..publishing.publisher import Publisher
from .pipeline import ASPECT_FOR_MEDIA, GenerationPipeline

log = get_logger("scheduling.worker")

_PREPARE_STATES = [JobStatus.SCHEDULED, JobStatus.DRAFT]
_PUBLISH_STATES = [JobStatus.MEDIA_READY, JobStatus.RETRY_PENDING,
                   JobStatus.CONTAINER_CREATED, JobStatus.UPLOADING, JobStatus.PUBLISHING]
_INFLIGHT = {JobStatus.RETRY_PENDING, JobStatus.CONTAINER_CREATED,
             JobStatus.UPLOADING, JobStatus.PUBLISHING}


@dataclass
class TickStats:
    planned: int = 0
    prepared: int = 0
    published: int = 0
    skipped: int = 0
    rescheduled: int = 0
    failed: int = 0
    review: int = 0
    messages: list[str] = field(default_factory=list)


class Worker:
    def __init__(self, settings: Settings, db: Database, registry: AccountRegistry, *,
                 pipeline: GenerationPipeline | None = None,
                 publisher: Publisher | None = None,
                 generate_ahead_days: int = 2,
                 prepare_ahead_minutes: int = 60,
                 try_comfyui: bool = True):
        self.s = settings
        self.db = db
        self.registry = registry
        self.pipeline = pipeline or GenerationPipeline(settings, db)
        self.publisher = publisher or Publisher(settings, db, registry)
        self.generate_ahead_days = generate_ahead_days
        self.prepare_ahead = timedelta(minutes=prepare_ahead_minutes)
        self.try_comfyui = try_comfyui

    # -- tick --------------------------------------------------------------
    def run_once(self) -> TickStats:
        stats = TickStats()
        self._plan(stats)
        self._prepare_media(stats)
        self._publish_due(stats)
        log.info("tick: planned=%d prepared=%d published=%d skipped=%d resched=%d "
                 "review=%d failed=%d", stats.planned, stats.prepared, stats.published,
                 stats.skipped, stats.rescheduled, stats.review, stats.failed)
        return stats

    def _plan(self, stats: TickStats) -> None:
        from .planner import plan_jobs
        rep = plan_jobs(self.db, self.registry, self.s, days=self.generate_ahead_days)
        stats.planned = rep.created

    # -- media preparation -------------------------------------------------
    def _prepare_media(self, stats: TickStats) -> None:
        now = now_utc()
        horizon = now + self.prepare_ahead
        jobs = self.db.list_jobs(statuses=_PREPARE_STATES)
        # group by (page_id, local date) so feed+story of a day share content
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for job in jobs:
            if not job.get("scheduled_at"):
                continue
            sched = parse_iso(job["scheduled_at"])
            if sched > horizon:
                continue  # too far ahead, prepare later
            page = self.registry.get(job["page_id"])
            local_date = sched.astimezone(ZoneInfo(page.publishing.timezone)).date()
            groups[(job["page_id"], local_date.isoformat())].append(job)

        for (page_id, _date), group in groups.items():
            page = self.registry.get(page_id)
            # reuse content already chosen for a sibling job, else select fresh
            content_id = next((j["content_id"] for j in group if j.get("content_id")), None)
            content = (self.db.get_content(content_id) if content_id
                       else select_content_for_page(self.db, page))
            if not content:
                stats.messages.append(f"no approved content for {page_id}")
                log.warning("No approved content available for %s", page_id)
                continue
            aspects = sorted({ASPECT_FOR_MEDIA[MediaType(j["media_type"])] for j in group})
            try:
                result = self.pipeline.generate(page, content, aspects=aspects,
                                                 try_comfyui=self.try_comfyui)
            except Exception as e:  # noqa: BLE001
                log.exception("pipeline failed for %s: %s", page_id, e)
                stats.failed += 1
                continue
            for job in group:
                vid = result.video_for(job["media_type"])
                if result.ok and vid:
                    self.db.update_job(job["id"], content_id=content["id"],
                                       output_path=vid, generated_at=utcnow_iso(),
                                       status=JobStatus.MEDIA_READY)
                    stats.prepared += 1
                else:
                    self.db.update_job(job["id"], content_id=content["id"],
                                       status=JobStatus.NEEDS_REVIEW,
                                       last_error=result.message or "quality not met")
                    stats.review += 1

    # -- publishing --------------------------------------------------------
    def _publish_due(self, stats: TickStats) -> None:
        now_iso = utcnow_iso()
        now_dt = now_utc()
        jobs = self.db.due_jobs(now_iso, statuses=_PUBLISH_STATES)
        for job in jobs:
            status = JobStatus(job["status"])
            if status not in _INFLIGHT:  # MEDIA_READY -> subject to missed policy
                action = self._missed_action(job, now_dt)
                if action == "wait":
                    continue
                if action == "skip":
                    self.db.update_job(job["id"], status=JobStatus.SKIPPED,
                                       last_error="missed schedule (policy skip)")
                    self.db.log_event(job_id=job["id"], page_id=job["page_id"],
                                      event="skip", error="missed schedule")
                    stats.skipped += 1
                    continue
                if action == "reschedule":
                    self._reschedule(job)
                    stats.rescheduled += 1
                    continue
            outcome = self.publisher.publish_job(job["id"])
            if outcome.status == JobStatus.PUBLISHED:
                stats.published += 1
            elif outcome.status == JobStatus.FAILED:
                stats.failed += 1

    def _missed_action(self, job: dict, now_dt) -> str:
        sched = parse_iso(job["scheduled_at"])
        if sched > now_dt:
            return "wait"
        page = self.registry.get(job["page_id"])
        policy = page.publishing.missed_job_policy
        delay_min = (now_dt - sched).total_seconds() / 60.0
        if policy == MissedJobPolicy.PUBLISH_IMMEDIATELY:
            return "publish"
        if policy == MissedJobPolicy.PUBLISH_WITHIN_WINDOW:
            return ("publish" if delay_min <= page.publishing.missed_job_window_minutes
                    else "skip")
        if policy == MissedJobPolicy.SKIP:
            return "skip"
        if policy == MissedJobPolicy.RESCHEDULE:
            return "reschedule"
        return "publish"

    def _reschedule(self, job: dict) -> None:
        sched = parse_iso(job["scheduled_at"]) + timedelta(days=1)
        self.db.update_job(job["id"],
                           scheduled_at=sched.replace(microsecond=0).isoformat().replace(
                               "+00:00", "Z"))
        self.db.log_event(job_id=job["id"], page_id=job["page_id"], event="reschedule")

    # -- recovery / loop ---------------------------------------------------
    def recover(self) -> int:
        """Log in-flight jobs after a restart. The publisher resumes them
        idempotently (reusing container_id / detecting existing media)."""
        stuck = self.db.list_jobs(statuses=list(_INFLIGHT))
        if stuck:
            log.info("recovery: %d in-flight job(s) will be resumed", len(stuck))
        return len(stuck)

    def run_forever(self, interval_seconds: float = 60.0,
                    stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        self.recover()
        log.info("Worker started (interval=%.0fs, mode=%s)", interval_seconds, self.s.mode)
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001
                log.exception("tick error: %s", e)
            stop_event.wait(interval_seconds)
        log.info("Worker stopped")
