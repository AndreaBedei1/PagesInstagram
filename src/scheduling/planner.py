"""Daily job planner: create idempotent publication_jobs per page/date/media_type."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta

from ..accounts.models import PageConfig
from ..accounts.registry import AccountRegistry
from ..core.enums import JobStatus, MediaType
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from ..core.timeutils import local_datetime, now_in, to_utc

log = get_logger("scheduling.planner")


@dataclass
class PlanReport:
    created: int = 0
    existing: int = 0
    days: int = 0
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"jobs created={self.created} existing={self.existing} days={self.days}"


def _media_types(page: PageConfig) -> list[MediaType]:
    types: list[MediaType] = []
    if page.publishing.publish_feed:
        types.append(MediaType.FEED_VIDEO)
    if page.publishing.publish_story:
        types.append(MediaType.STORY_VIDEO)
    if page.publishing.publish_reel:
        types.append(MediaType.REEL)
    return types


def plan_page(db, page: PageConfig, *, start: date_cls, days: int) -> PlanReport:
    report = PlanReport(days=days)
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
              days: int = 1) -> PlanReport:
    """Plan jobs for all enabled pages for ``days`` days starting today (page tz)."""
    total = PlanReport(days=days)
    for page in registry.enabled():
        if db.is_page_paused(page.page_id):
            continue
        start = now_in(page.publishing.timezone).date()
        rep = plan_page(db, page, start=start, days=days)
        total.created += rep.created
        total.existing += rep.existing
    log.info("Planned: %s", total.summary())
    return total
