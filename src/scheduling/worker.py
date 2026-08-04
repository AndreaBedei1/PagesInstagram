"""Persistent worker: plan jobs, generate media ahead of time, publish when due.

One tick = plan() -> prepare_media() -> publish_due(). ``run_forever`` loops with
a stop flag. Missed jobs (PC was off at the scheduled time) are handled per the
page's ``missed_job_policy``. A single-instance lock prevents two workers running.
"""
from __future__ import annotations

import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

from ..accounts.registry import AccountRegistry
from ..core.enums import JobStatus, MissedJobPolicy, Mode
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from ..core.timeutils import now_utc, parse_iso, utcnow_iso
from ..database import Database
from ..content.service import (SelectionError, select_content_for_date,
                               select_content_for_page)
from ..publishing.publisher import Publisher
from .pipeline import GenerationPipeline

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
    cleaned: int = 0
    messages: list[str] = field(default_factory=list)


class Worker:
    def __init__(self, settings: Settings, db: Database, registry: AccountRegistry, *,
                 pipeline: GenerationPipeline | None = None,
                 publisher: Publisher | None = None,
                 generate_ahead_days: int | None = None,
                 prepare_ahead_minutes: int | None = None,
                 try_comfyui: bool = True,
                 cleanup_enabled: bool = True):
        self.s = settings
        self.db = db
        self.registry = registry
        self.pipeline = pipeline or GenerationPipeline(settings, db)
        self.publisher = publisher or Publisher(settings, db, registry)
        #: ``None`` => each page's own ``generation.planning_horizon_days``.
        self.generate_ahead_days = generate_ahead_days
        #: ``None`` => each page's own ``generation.prepare_ahead_days``.
        self.prepare_ahead = (timedelta(minutes=prepare_ahead_minutes)
                              if prepare_ahead_minutes is not None else None)
        self.try_comfyui = try_comfyui
        self.cleanup_enabled = cleanup_enabled

    # -- tick --------------------------------------------------------------
    def run_once(self) -> TickStats:
        stats = TickStats()
        self._plan(stats)
        self._prepare_media(stats)
        self._publish_due(stats)
        if self.cleanup_enabled:
            self._cleanup_media(stats)
        log.info("tick: planned=%d prepared=%d published=%d skipped=%d resched=%d "
                 "review=%d failed=%d cleaned=%d", stats.planned, stats.prepared,
                 stats.published, stats.skipped, stats.rescheduled, stats.review,
                 stats.failed, stats.cleaned)
        return stats

    def _plan(self, stats: TickStats) -> None:
        from .planner import plan_jobs
        rep = plan_jobs(self.db, self.registry, self.s, days=self.generate_ahead_days)
        stats.planned = rep.created

    def _prepare_horizon(self, page) -> timedelta:
        """How far ahead media is generated for this page (rolling buffer)."""
        if self.prepare_ahead is not None:
            return self.prepare_ahead
        return timedelta(days=page.generation.prepare_ahead_days)

    # -- media preparation -------------------------------------------------
    def _prepare_media(self, stats: TickStats) -> None:
        now = now_utc()
        jobs = self.db.list_jobs(statuses=_PREPARE_STATES)
        # group by (page_id, local date): Reel + Story of a day MUST share content
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for job in jobs:
            if not job.get("scheduled_at") or self.db.is_page_paused(job["page_id"]):
                continue
            page = self.registry.get(job["page_id"])
            if parse_iso(job["scheduled_at"]) > now + self._prepare_horizon(page):
                continue  # beyond the rolling buffer, prepare on a later tick
            local_date = parse_iso(job["scheduled_at"]).astimezone(
                ZoneInfo(page.publishing.timezone)).date().isoformat()
            groups[(job["page_id"], local_date)].append(job)

        for (page_id, local_date), group in sorted(groups.items()):
            page = self.registry.get(page_id)
            resolved = self._resolve_daily_content(page, local_date, stats, group)
            if resolved is None:
                continue
            content, cycle_number = resolved
            daily = self.db.get_daily_content(page_id, local_date)

            # Generate the shared 9:16 video once; reuse if already produced.
            video_path = daily.get("video_path") if daily else None
            if not (video_path and os.path.exists(video_path)):
                try:
                    result = self.pipeline.generate_daily(
                        page, content, music_track_id=(daily or {}).get("music_track_id"),
                        try_comfyui=self.try_comfyui, scheduled_date=local_date,
                        cycle_number=cycle_number)
                except Exception as e:  # noqa: BLE001
                    log.exception("pipeline failed for %s: %s", page_id, e)
                    stats.failed += 1
                    continue
                if not result.ok:
                    self._route_to_review(group, page_id, result.message, stats,
                                          content_id=content["id"])
                    continue
                video_path = result.video_path
                self.db.update_daily_content(
                    daily["id"], content_id=content["id"],
                    music_track_id=result.music_track_id,
                    media_asset_id=result.media_asset_id, video_path=video_path)

            # Assign the SAME video to every job of the day (Reel + Story).
            for job in group:
                self.db.update_job(job["id"], content_id=content["id"],
                                   output_path=video_path, generated_at=utcnow_iso(),
                                   status=JobStatus.MEDIA_READY,
                                   upload_method=page.publishing.upload_method)
                stats.prepared += 1

    def _route_to_review(self, group: list[dict], page_id: str, message: str,
                         stats: TickStats, content_id: int | None = None) -> None:
        for job in group:
            fields = {"status": JobStatus.NEEDS_REVIEW, "last_error": message}
            if content_id is not None:
                fields["content_id"] = content_id
            self.db.update_job(job["id"], **fields)
            self.db.log_event(job_id=job["id"], page_id=page_id, event="error",
                              error=message)
            stats.review += 1

    def _resolve_daily_content(self, page, local_date: str, stats: TickStats,
                               group: list[dict]) -> tuple[dict, int] | None:
        """Persistent page+date -> content assignment (feed & story never diverge).

        The assignment is written to ``daily_content`` the first time and reused
        afterwards, so a crash mid-day cannot change what gets published. For the
        deterministic policies the stored row and a fresh computation always
        agree anyway — the row is a cache, not the source of truth.
        """
        daily = self.db.get_daily_content(page.page_id, local_date)
        if daily and daily.get("content_id"):
            content = self.db.get_content(daily["content_id"])
            if content:
                return content, int(daily.get("cycle_number") or 0)

        try:
            sel = select_content_for_date(self.db, page, local_date)
        except SelectionError as e:
            stats.messages.append(str(e))
            log.warning("selection failed: %s", e)
            self._route_to_review(group, page.page_id, str(e), stats)
            return None

        daily_id, _ = self.db.create_daily_content(page.page_id, local_date,
                                                   sel.content_id)
        self.db.update_daily_content(daily_id, cycle_number=sel.cycle_number,
                                     sequence_index=sel.sequence_index)
        return sel.content, sel.cycle_number

    # -- rolling retention -------------------------------------------------
    def _cleanup_media(self, stats: TickStats) -> None:
        """Delete local media of jobs published longer ago than the retention.

        Database rows, logs and metadata are kept. Files belonging to FAILED or
        NEEDS_REVIEW jobs are never touched, and a file shared by several jobs
        (Reel + Story of the same day) is removed only once every referencing
        job is published.
        """
        now = now_utc()
        for page in self.registry.all():
            days = page.generation.published_media_retention_days
            if days <= 0:
                continue
            cutoff = (now - timedelta(days=days)).replace(
                microsecond=0).isoformat().replace("+00:00", "Z")
            for job in self.db.jobs_pending_media_cleanup(cutoff):
                if job["page_id"] != page.page_id:
                    continue
                path = job.get("output_path")
                if path and self.db.paths_still_referenced(path) == 0:
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                            stats.cleaned += 1
                    except OSError as e:  # noqa: PERF203 — per-file failure is fine
                        log.warning("retention: impossibile eliminare %s: %s", path, e)
                        continue
                self.db.update_job(job["id"], media_deleted_at=utcnow_iso())

    # -- publishing --------------------------------------------------------
    def _publish_due(self, stats: TickStats) -> None:
        if self.s.mode == Mode.TEST:
            # test mode: publishing is manual only (CLI 'instagram publish-job --confirm')
            return
        now_iso = utcnow_iso()
        now_dt = now_utc()
        jobs = self.db.due_jobs(now_iso, statuses=_PUBLISH_STATES)
        for job in jobs:
            if self.db.is_page_paused(job["page_id"]):
                continue
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
