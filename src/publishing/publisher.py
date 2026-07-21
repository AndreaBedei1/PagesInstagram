"""High-level publisher: drives a job through the idempotent state machine.

Modes:
  - dry_run    : NO network. Marks the job PUBLISHED with a DRYRUN media id so
                 the pipeline completes end-to-end and stays idempotent.
  - test       : uses an injected client (mock or a real test account). Publishes
                 only when a client is provided.
  - production : resolves real credentials, builds a GraphClient, publishes.

Idempotency: a job already PUBLISHED (or holding a remote_media_id) is never
re-published; an existing container_id is reused instead of recreating one.
Retries use exponential backoff and only apply to transient (retryable) errors.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..accounts.models import PageConfig
from ..accounts.registry import AccountRegistry
from ..content.captions import build_caption
from ..core.enums import META_MEDIA_TYPE, JobStatus, MediaType, Mode
from ..core.errors import PublishError
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from ..core.timeutils import now_utc, parse_iso, utcnow_iso
from ..database import Database
from .credentials import resolve_credentials
from .graph_client import GraphClient

log = get_logger("publishing.publisher")

_VIDEO_TYPES = {MediaType.FEED_VIDEO, MediaType.STORY_VIDEO, MediaType.REEL}
_STORY_TYPES = {MediaType.STORY_IMAGE, MediaType.STORY_VIDEO}


@dataclass
class PublishTarget:
    client: object
    ig_user_id: str


@dataclass
class PublishOutcome:
    job_id: int
    status: str
    mode: str
    remote_media_id: str | None = None
    container_id: str | None = None
    message: str = ""


class Publisher:
    def __init__(self, settings: Settings, db: Database, registry: AccountRegistry,
                 *, client_factory: Callable[[PageConfig], PublishTarget | None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.s = settings
        self.db = db
        self.registry = registry
        self._factory = client_factory or self._default_factory
        self._sleep = sleep

    # -- helpers -----------------------------------------------------------
    def _default_factory(self, page: PageConfig) -> PublishTarget | None:
        creds = resolve_credentials(page)
        if not creds:
            return None
        client = GraphClient(
            creds.access_token,
            api_version=self.s.publishing.graph_api_version,
            flavor=self.s.publishing.api_flavor,
        )
        return PublishTarget(client=client, ig_user_id=creds.ig_user_id)

    def media_url_for(self, output_path: str) -> str:
        base = self.s.publishing.public_media_base_url.rstrip("/")
        if not base:
            raise PublishError(
                "ICE_PUBLIC_MEDIA_BASE_URL non impostato: la Graph API richiede "
                "URL pubblici dei media (vedi README)."
            )
        try:
            rel = Path(output_path).resolve().relative_to(self.s.paths.generated.resolve())
        except ValueError:
            rel = Path(output_path).name
        return f"{base}/{Path(rel).as_posix()}"

    def _caption_for(self, job: dict, page: PageConfig) -> str | None:
        if job.get("content_id") is None:
            return None
        content = self.db.get_content(job["content_id"])
        if not content:
            return None
        return build_caption(content, content_type=page.content_type)

    # -- main --------------------------------------------------------------
    def publish_job(self, job_id: int) -> PublishOutcome:
        job = self.db.get_job(job_id)
        if job is None:
            raise PublishError(f"job {job_id} not found")
        page = self.registry.get(job["page_id"])
        mode = self.s.mode

        # Idempotency: already published?
        if job["status"] == JobStatus.PUBLISHED or job.get("remote_media_id"):
            return PublishOutcome(job_id, JobStatus.PUBLISHED, mode,
                                  remote_media_id=job.get("remote_media_id"),
                                  container_id=job.get("container_id"),
                                  message="already published (idempotent)")

        if mode == Mode.DRY_RUN:
            return self._dry_run(job, page)

        target = self._factory(page)
        if target is None:
            self.db.update_job(job_id, status=JobStatus.FAILED,
                               last_error="missing credentials (env)")
            self.db.log_event(job_id=job_id, page_id=page.page_id, event="error",
                              error="missing credentials")
            return PublishOutcome(job_id, JobStatus.FAILED, mode,
                                  message="missing credentials")
        return self._do_publish(job, page, target)

    def _dry_run(self, job: dict, page: PageConfig) -> PublishOutcome:
        jid = job["id"]
        caption = self._caption_for(job, page) or ""
        fake_id = f"DRYRUN-{job['idempotency_key']}"
        self.db.update_job(jid, status=JobStatus.PUBLISHED, remote_media_id=fake_id,
                           published_at=utcnow_iso())
        self.db.log_event(job_id=jid, page_id=page.page_id, event="dry_run_publish",
                          request_summary=f"would publish {job['media_type']} "
                                          f"path={job.get('output_path')} "
                                          f"caption_len={len(caption)}",
                          response_summary=fake_id)
        log.info("[DRY-RUN] would publish job %s (%s) for %s", jid, job["media_type"],
                 page.page_id)
        return PublishOutcome(jid, JobStatus.PUBLISHED, Mode.DRY_RUN,
                              remote_media_id=fake_id, message="dry-run (no API call)")

    def _do_publish(self, job: dict, page: PageConfig,
                    target: PublishTarget) -> PublishOutcome:
        jid = job["id"]
        media_type = MediaType(job["media_type"])
        meta_type = META_MEDIA_TYPE[media_type]
        is_video = media_type in _VIDEO_TYPES
        client = target.client
        try:
            media_url = self.media_url_for(job["output_path"])

            container_id = job.get("container_id")
            if not container_id:
                self.db.update_job(jid, status=JobStatus.UPLOADING)
                kwargs: dict = {"media_type": meta_type}
                if is_video:
                    kwargs["video_url"] = media_url
                else:
                    kwargs["image_url"] = media_url
                if media_type not in _STORY_TYPES:  # stories ignore caption
                    kwargs["caption"] = self._caption_for(job, page)
                container_id = client.create_media_container(target.ig_user_id, **kwargs)
                self.db.update_job(jid, container_id=container_id,
                                   status=JobStatus.CONTAINER_CREATED)
                self.db.log_event(job_id=jid, page_id=page.page_id,
                                  event="container_create",
                                  request_summary=f"{meta_type} url={media_url}",
                                  response_summary=container_id)

            self._await_finished(client, container_id)

            self.db.update_job(jid, status=JobStatus.PUBLISHING)
            media_id = client.publish_container(target.ig_user_id, container_id)
            self.db.update_job(jid, remote_media_id=media_id,
                               status=JobStatus.PUBLISHED, published_at=utcnow_iso())
            self.db.log_event(job_id=jid, page_id=page.page_id, event="publish",
                              response_summary=media_id)
            log.info("Published job %s -> media %s (%s)", jid, media_id, page.page_id)
            return PublishOutcome(jid, JobStatus.PUBLISHED, self.s.mode,
                                  remote_media_id=media_id, container_id=container_id,
                                  message="published")
        except PublishError as e:
            return self._handle_failure(job, page, e)

    def _await_finished(self, client, container_id: str) -> None:
        interval = self.s.publishing.status_poll_interval_seconds
        max_s = self.s.publishing.status_poll_max_seconds
        waited = 0.0
        while waited <= max_s:
            status = client.get_container_status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(f"container status {status}",
                                   retryable=(status == "ERROR"), code=status)
            self._sleep(interval)
            waited += interval
        raise PublishError("container status polling timeout", retryable=True)

    def _handle_failure(self, job: dict, page: PageConfig,
                        e: PublishError) -> PublishOutcome:
        jid = job["id"]
        retry_count = int(job.get("retry_count") or 0) + 1
        # A failed container must be recreated on the next attempt.
        clear_container = e.code in ("ERROR", "EXPIRED")
        if e.retryable and retry_count <= self.s.publishing.max_retries:
            backoff = min(self.s.publishing.backoff_max_seconds,
                          self.s.publishing.backoff_base_seconds * (2 ** (retry_count - 1)))
            next_at = (now_utc().timestamp() + backoff)
            from datetime import datetime, timezone
            next_iso = datetime.fromtimestamp(next_at, tz=timezone.utc).replace(
                microsecond=0).isoformat().replace("+00:00", "Z")
            fields = dict(status=JobStatus.RETRY_PENDING, retry_count=retry_count,
                          last_error=str(e), next_retry_at=next_iso)
            if clear_container:
                fields["container_id"] = None
            self.db.update_job(jid, **fields)
            self.db.log_event(job_id=jid, page_id=page.page_id, event="retry",
                              error=str(e))
            log.warning("Job %s retry %s/%s in %.0fs: %s", jid, retry_count,
                        self.s.publishing.max_retries, backoff, e)
            return PublishOutcome(jid, JobStatus.RETRY_PENDING, self.s.mode,
                                  message=f"retry scheduled: {e}")
        fields = dict(status=JobStatus.FAILED, retry_count=retry_count, last_error=str(e))
        if clear_container:
            fields["container_id"] = None
        self.db.update_job(jid, **fields)
        self.db.log_event(job_id=jid, page_id=page.page_id, event="error", error=str(e))
        log.error("Job %s FAILED: %s", jid, e)
        return PublishOutcome(jid, JobStatus.FAILED, self.s.mode, message=str(e))

    # -- optional limit check ---------------------------------------------
    def publishing_limit(self, page: PageConfig) -> dict | None:
        target = self._factory(page)
        if target is None:
            return None
        try:
            return target.client.get_publishing_limit(target.ig_user_id)
        except PublishError as e:
            log.warning("publishing_limit failed: %s", e)
            return None
