"""Daily job planner: create idempotent publication_jobs per page/date/media_type."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta

from ..accounts.models import PageConfig
from ..accounts.registry import AccountRegistry
from ..core.enums import TERMINAL_JOB_STATES, JobStatus, MediaType
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from ..core.timeutils import local_datetime, now_in, to_utc

log = get_logger("scheduling.planner")


@dataclass
class PlanReport:
    created: int = 0
    existing: int = 0
    days: int = 0
    #: Future jobs in a format the page no longer publishes, set to SKIPPED.
    retired: int = 0
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        base = (f"jobs created={self.created} existing={self.existing} "
                f"days={self.days}")
        return base + (f" ritirati={self.retired}" if self.retired else "")


def _media_types(page: PageConfig) -> list[MediaType]:
    """What this page publishes each day, in the format it has chosen.

    ``feed_media_type: IMAGE`` gives a 4:5 still; ``REELS`` gives the 9:16
    video. The planner has to agree with the pipeline about this, because the
    job row carries the media type from here all the way to Meta.
    """
    types: list[MediaType] = []
    if page.publishing.publish_feed or page.publishing.publish_reel:
        if str(page.publishing.feed_media_type).upper() == "IMAGE":
            types.append(MediaType.FEED_IMAGE)
        else:
            types.append(MediaType.REEL)
    if page.publishing.publish_story:
        types.append(MediaType.STORY_VIDEO)
    return types


def retire_obsolete_jobs(db, page: PageConfig, *, start: date_cls) -> int:
    """Skip any pending job in a format this page no longer publishes.

    The idempotency key carries the media type, so changing ``feed_media_type``
    does not rewrite the pending jobs: it plans *new* ones beside them. The
    buffer then holds a Reel and an image post in the same slot, and an armed
    page would publish twice a day — which is exactly what the buffer showed
    after this migration.

    A job whose slot has already gone by is retired too, and not only for
    tidiness: ``missed_job_policy: publish_within_window`` gives a late job
    several hours of grace, so a Reel scheduled this morning could still go out
    this afternoon, in a format the page no longer publishes.

    They are marked ``SKIPPED``, not deleted: the row is history, and a
    terminal state is what stops the worker from ever touching it again.
    Anything already published is left alone.
    """
    wanted = {str(t) for t in _media_types(page)}
    retired = 0
    for job in db.list_jobs(page_id=page.page_id):
        if str(job.get("media_type")) in wanted:
            continue
        if job.get("status") in TERMINAL_JOB_STATES or job.get("remote_media_id"):
            continue
        db.update_job(job["id"], status=JobStatus.SKIPPED,
                      last_error=(f"formato {job.get('media_type')} non più "
                                  f"pubblicato da questa pagina"))
        db.log_event(job_id=job["id"], page_id=page.page_id, event="skipped",
                     request_summary=f"media_type={job.get('media_type')}")
        retired += 1
    if retired:
        log.info("%s: %d job futuri in un formato non più pubblicato messi in "
                 "SKIPPED", page.page_id, retired)
    return retired


def plan_page(db, page: PageConfig, *, start: date_cls, days: int) -> PlanReport:
    report = PlanReport(days=days)
    report.retired = retire_obsolete_jobs(db, page, start=start)
    tz = page.publishing.timezone
    feed_h, feed_m = page.publishing.feed_time_tuple()
    story_h, story_m = page.publishing.story_time_tuple()
    for offset in range(days):
        d = start + timedelta(days=offset)
        for media_type in _media_types(page):
            if media_type == MediaType.STORY_VIDEO:
                hh, mm = story_h, story_m
            else:
                hh, mm = feed_h, feed_m
            local_dt = local_datetime(d.year, d.month, d.day, hh, mm, tz)
            scheduled_utc = to_utc(local_dt).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z")
            key = f"{page.page_id}:{media_type}:{d.isoformat()}"
            _, created = db.create_job(
                page_id=page.page_id, content_id=None, media_type=media_type,
                idempotency_key=key, scheduled_at=scheduled_utc,
                status=JobStatus.SCHEDULED,
            )
            if created:
                report.created += 1
            else:
                report.existing += 1
    return report


def plan_jobs(db, registry: AccountRegistry, settings: Settings, *,
              days: int | None = None) -> PlanReport:
    """Plan jobs for all enabled pages, starting today in each page's timezone.

    ``days=None`` (the worker's normal call) uses each page's own
    ``generation.planning_horizon_days`` — that is the rolling job buffer. An
    explicit ``days`` overrides it (used by ``ice schedule --days N``).
    """
    total = PlanReport(days=days or 0)
    for page in registry.enabled():
        if db.is_page_paused(page.page_id):
            continue
        horizon = days if days is not None else page.generation.planning_horizon_days
        start = now_in(page.publishing.timezone).date()
        rep = plan_page(db, page, start=start, days=horizon)
        total.created += rep.created
        total.existing += rep.existing
        total.retired += rep.retired
        total.days = max(total.days, horizon)
    log.info("Planned: %s", total.summary())
    return total
