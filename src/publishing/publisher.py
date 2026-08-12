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

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..accounts.models import PageConfig
from ..accounts.registry import AccountRegistry
from ..content.captions import build_caption
from ..core.enums import (IMAGE_TYPES, META_MEDIA_TYPE, JobStatus,
                          MediaType, Mode, UploadMethod, UploadStatus)
from ..core.errors import PublishError
from ..core.meta_api import (IMAGE_MAX_FILE_BYTES, REEL_MAX_FILE_BYTES,
                             REEL_MAX_SECONDS,
                             REEL_MIN_SECONDS, STORY_MAX_SECONDS,
                             STORY_MIN_SECONDS)
from .arming import may_publish
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


def build_publish_target(settings: Settings, page: PageConfig) -> "PublishTarget | None":
    """Build a real GraphClient target from env credentials, or None if missing."""
    creds = resolve_credentials(page)
    if not creds:
        return None
    flavor = getattr(page.instagram, "api_flavor", None) or settings.publishing.api_flavor
    client = GraphClient(
        creds.access_token,
        api_version=settings.publishing.graph_api_version,
        flavor=flavor,
        upload_timeout=settings.publishing.upload_timeout_seconds,
    )
    return PublishTarget(client=client, ig_user_id=creds.ig_user_id)


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
        return build_publish_target(self.s, page)

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

        # Production mode is one variable, and one variable is one mistake away
        # from five accounts posting at once. A page also has to be armed, by
        # hand, after its own controlled canary.
        allowed, reason = may_publish(self.db, page.page_id, mode)
        if not allowed:
            self.db.update_job(job_id, status=JobStatus.NEEDS_REVIEW,
                               last_error=reason)
            self.db.log_event(job_id=job_id, page_id=page.page_id,
                              event="blocked", error=reason)
            return PublishOutcome(job_id, JobStatus.NEEDS_REVIEW, mode,
                                  message=reason)

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

    def _upload_method(self, job: dict, page: PageConfig) -> str:
        """Which transport this job uses now — not which one it was planned with.

        The per-job column exists so an interrupted upload resumes the way it
        started; it is not a record of intent. Letting it win unconditionally
        meant a job planned days ago kept using the transport that was
        configured then, and silently ignored a page that had since been
        migrated. That is how the second canary went down the resumable path
        and got ProcessingFailedError from a configuration nobody had asked for
        any more.

        So the stored value only decides while there is something in flight.
        """
        if job.get("container_id"):
            stored = job.get("upload_method")
            if stored:
                return stored
        return (page.publishing.upload_method or self.s.publishing.upload_method
                or UploadMethod.RESUMABLE)

    def publish_canary_media(self, job: dict, page: PageConfig,
                             target: PublishTarget) -> PublishOutcome:
        """The publication itself, for the canary and for nothing else.

        ``publish_job`` refuses an unarmed page, and a page is only armed after
        its canary — the circular dependency ``publishing/canary.py`` exists to
        break. The resumable canary breaks it by calling ``publish_container``
        on the client directly; a Quick Tunnel publication cannot do that,
        because container creation and publication are one indivisible act
        inside one tunnel session. So it needs the publisher's own path.

        This is not a bypass switch: it takes no flag, it reads no setting, and
        the gate that replaces arming here (``canary.check_publish_allowed``) is
        strictly stronger — an explicit page, an explicit job, an interactive
        process, a typed word, a green audit, a green health check, and never
        twice. ``tests/unit/test_go_live_guards.py`` fails if anything other
        than ``canary.py`` calls this.
        """
        return self._do_publish(job, page, target)

    def _do_publish(self, job: dict, page: PageConfig,
                    target: PublishTarget) -> PublishOutcome:
        media_type = MediaType(job["media_type"])
        meta_type = META_MEDIA_TYPE[media_type]
        try:
            self._verify_account(target, page, media_type)
            if self._upload_method(job, page) == UploadMethod.RESUMABLE:
                return self._publish_resumable(job, page, target, media_type, meta_type)
            return self._publish_hosted(job, page, target, media_type, meta_type)
        except PublishError as e:
            return self._handle_failure(job, page, e)

    # -- account & media pre-checks ---------------------------------------
    def _verify_account(self, target: PublishTarget, page: PageConfig,
                        media_type: MediaType) -> None:
        try:
            info = target.client.get_account_info(target.ig_user_id)
        except PublishError:
            return  # metadata call failed — don't block publishing on it
        atype = (info.get("account_type") or "").lower()
        if atype == "personal":
            raise PublishError("account personale: pubblicazione via API non supportata",
                               retryable=False, code="ACCOUNT_TYPE")
        if media_type == MediaType.STORY_VIDEO and atype and atype != "business":
            raise PublishError(f"le Stories via API richiedono un account business "
                               f"(trovato: {atype})", retryable=False, code="ACCOUNT_TYPE")

    def _validate_image_file(self, file_path: str | None) -> int:
        """A still has no duration, no codec and no audio to get wrong."""
        if not file_path or not os.path.exists(file_path):
            raise PublishError(f"file media mancante: {file_path}", retryable=False,
                               code="FILE_MISSING")
        size = os.path.getsize(file_path)
        if size <= 0:
            raise PublishError("file media vuoto", retryable=False, code="FILE_EMPTY")
        if not str(file_path).lower().endswith((".jpg", ".jpeg", ".png")):
            raise PublishError("un post immagine richiede JPEG o PNG",
                               retryable=False, code="FILE_FORMAT")
        if size > IMAGE_MAX_FILE_BYTES:
            raise PublishError(
                f"immagine di {size / 1_000_000:.0f} MB: il massimo documentato "
                f"e {IMAGE_MAX_FILE_BYTES // 1_000_000} MB", retryable=False,
                code="FILE_SIZE")
        return size

    def _validate_media_file(self, file_path: str | None,
                             media_type: MediaType) -> int:
        """Validate by kind. A PNG has no duration to be out of range."""
        if media_type in IMAGE_TYPES:
            return self._validate_image_file(file_path)
        return self._validate_video_file(file_path, media_type)

    def _validate_video_file(self, file_path: str | None, media_type: MediaType) -> int:
        if not file_path or not os.path.exists(file_path):
            raise PublishError(f"file media mancante: {file_path}", retryable=False,
                               code="FILE_MISSING")
        size = os.path.getsize(file_path)
        if size <= 0:
            raise PublishError("file media vuoto", retryable=False, code="FILE_EMPTY")
        if not str(file_path).lower().endswith((".mp4", ".mov")):
            raise PublishError("il resumable upload richiede MP4/MOV", retryable=False,
                               code="FILE_FORMAT")
        if size > REEL_MAX_FILE_BYTES:
            raise PublishError(
                f"file di {size / 1_000_000:.0f} MB: il massimo documentato è "
                f"{REEL_MAX_FILE_BYTES // 1_000_000} MB", retryable=False,
                code="FILE_SIZE")
        try:
            from ..video import ffmpeg as ffmpeg_mod
            dur = ffmpeg_mod.probe_media(str(file_path)).duration or 0.0
        except Exception:  # noqa: BLE001 — probing must not block on odd ffmpeg output
            dur = 0.0
        if dur:
            if media_type == MediaType.REEL and not (
                    REEL_MIN_SECONDS <= dur <= REEL_MAX_SECONDS):
                raise PublishError(
                    f"durata Reel {dur:.1f}s fuori dai limiti "
                    f"{REEL_MIN_SECONDS}–{REEL_MAX_SECONDS}s",
                    retryable=False, code="DURATION")
            if media_type == MediaType.STORY_VIDEO and not (
                    STORY_MIN_SECONDS <= dur <= STORY_MAX_SECONDS):
                raise PublishError(
                    f"durata Story {dur:.1f}s fuori dai limiti "
                    f"{STORY_MIN_SECONDS}–{STORY_MAX_SECONDS}s",
                    retryable=False, code="DURATION")
        return size

    # -- resumable (direct upload) path -----------------------------------
    def _publish_resumable(self, job: dict, page: PageConfig, target: PublishTarget,
                           media_type: MediaType, meta_type: str) -> PublishOutcome:
        jid = job["id"]
        client = target.client
        file_path = job["output_path"]
        size = self._validate_video_file(file_path, media_type)
        is_reel = media_type == MediaType.REEL
        caption = self._caption_for(job, page) if is_reel else None  # no caption on Story
        share = page.publishing.share_reel_to_feed if is_reel else None

        container_id = job.get("container_id")
        upload_uri = job.get("upload_uri")

        # Reuse or recreate an existing container (crash-before-upload safe).
        if container_id:
            try:
                status = client.get_container_status(container_id)
            except PublishError:
                status = "UNKNOWN"
            if status == "PUBLISHED":
                self.db.update_job(jid, status=JobStatus.PUBLISHED,
                                   published_at=utcnow_iso(),
                                   remote_media_id=job.get("remote_media_id") or "PUBLISHED")
                return PublishOutcome(jid, JobStatus.PUBLISHED, self.s.mode,
                                      remote_media_id=job.get("remote_media_id"),
                                      container_id=container_id,
                                      message="already published (idempotent)")
            if status in ("EXPIRED", "ERROR"):
                container_id = upload_uri = None
                self.db.update_job(jid, container_id=None, upload_uri=None,
                                   upload_offset=0)

        if not container_id:
            self.db.update_job(jid, status=JobStatus.UPLOADING,
                               upload_method=UploadMethod.RESUMABLE,
                               upload_status=UploadStatus.IN_PROGRESS,
                               upload_size_bytes=size, upload_started_at=utcnow_iso())
            container_id, upload_uri = client.create_resumable_container(
                target.ig_user_id, media_type=meta_type, caption=caption,
                share_to_feed=share)
            self.db.update_job(jid, container_id=container_id, upload_uri=upload_uri,
                               status=JobStatus.CONTAINER_CREATED)
            self.db.log_event(job_id=jid, page_id=page.page_id, event="container_create",
                              request_summary=f"{meta_type} resumable share_to_feed={share} "
                                              f"size={size}",
                              response_summary=container_id)

        # Resume-aware binary upload. Remote is the source of truth for the offset.
        offset = int(job.get("upload_offset") or 0)
        try:
            st = client.get_upload_status(container_id)
            if st.get("bytes_transferred") is not None:
                offset = max(offset, int(st["bytes_transferred"]))
            if st.get("status_code") == "FINISHED":
                offset = size
        except PublishError:
            pass

        if offset < size:
            self.db.update_job(jid, status=JobStatus.UPLOADING, upload_offset=offset)
            client.upload_video_file(upload_uri, file_path, offset=offset,
                                     timeout=self.s.publishing.upload_timeout_seconds)
            self.db.log_event(job_id=jid, page_id=page.page_id, event="upload",
                              request_summary=f"offset={offset} size={size}",
                              response_summary="success")
        self.db.update_job(jid, upload_offset=size, upload_status=UploadStatus.COMPLETED,
                           upload_completed_at=utcnow_iso())

        self._await_finished(client, container_id)
        return self._finalize_publish(job, page, target, container_id)

    # -- hosted_url path ---------------------------------------------------
    def hosted_url_provider(self, page: PageConfig) -> str:
        return (getattr(page.publishing, "hosted_url_provider", "")
                or self.s.publishing.hosted_url_provider or "public_base_url")

    def _publish_hosted(self, job: dict, page: PageConfig, target: PublishTarget,
                        media_type: MediaType, meta_type: str) -> PublishOutcome:
        if self.hosted_url_provider(page) == "cloudflare_quick_tunnel":
            return self._publish_via_quick_tunnel(job, page, target, media_type,
                                                  meta_type)
        return self._publish_hosted_static(job, page, target, media_type, meta_type)

    def _publish_via_quick_tunnel(self, job: dict, page: PageConfig,
                                  target: PublishTarget, media_type: MediaType,
                                  meta_type: str) -> PublishOutcome:
        """Publish through a tunnel that exists only for this publication.

        The tunnel has to stay up until the container reports FINISHED, because
        that is when Meta has finished *fetching* the file. Closing after the
        POST would leave Meta downloading from an address that no longer
        resolves. It stays up through the publish call too — a few extra
        seconds against the risk of a half-completed publication.

        A container left over from a previous attempt is a special case: its
        ``video_url`` pointed at a tunnel that is now gone, so unless it already
        reached FINISHED it can never complete, and reusing it would wait for
        something that will not happen.
        """
        from .quick_tunnel import TunnelSession

        jid = job["id"]
        client = target.client
        self._validate_media_file(job.get("output_path"), media_type)

        existing = job.get("container_id")
        if existing:
            try:
                status = client.get_container_status(existing)
            except PublishError:
                status = "UNKNOWN"
            if status == "FINISHED":
                log.info("job %s: container %s già pronto, pubblico senza tunnel",
                         jid, existing)
                return self._finalize_publish(job, page, target, existing)
            log.info("job %s: container %s in stato %s con un tunnel ormai chiuso: "
                     "lo scarto", jid, existing, status)
            self.db.update_job(jid, container_id=None, upload_uri=None,
                               upload_offset=0)
            job = self.db.get_job(jid)

        # A feed post carries a caption; a Story does not. share_to_feed is a
        # Reel-only parameter and Meta rejects it on an IMAGE container.
        captioned = media_type not in _STORY_TYPES
        caption = self._caption_for(job, page) if captioned else None
        share = (page.publishing.share_reel_to_feed
                 if media_type == MediaType.REEL else None)

        is_image = media_type in IMAGE_TYPES
        with TunnelSession(job["output_path"], self.s.paths) as session:
            kwargs: dict = {"media_type": meta_type}
            # An image post is created with image_url and nothing about video:
            # no REELS type, no share_to_feed, no duration, no codec.
            kwargs["image_url" if is_image else "video_url"] = session.public_url
            if caption is not None:
                kwargs["caption"] = caption
            if share is not None and not is_image:
                kwargs["share_to_feed"] = share
            self.db.update_job(jid, status=JobStatus.UPLOADING,
                               upload_method=UploadMethod.HOSTED_URL)
            container_id = client.create_media_container(target.ig_user_id, **kwargs)
            self.db.update_job(jid, container_id=container_id,
                               status=JobStatus.CONTAINER_CREATED)
            self.db.log_event(job_id=jid, page_id=page.page_id,
                              event="container_create",
                              request_summary=f"{meta_type} quick_tunnel "
                                              f"share_to_feed={share}",
                              response_summary=container_id)
            self._await_finished(client, container_id)
            return self._finalize_publish(job, page, target, container_id)

    def _publish_hosted_static(self, job: dict, page: PageConfig,
                               target: PublishTarget, media_type: MediaType,
                               meta_type: str) -> PublishOutcome:
        jid = job["id"]
        client = target.client
        is_video = media_type in _VIDEO_TYPES
        media_url = self.media_url_for(job["output_path"])
        container_id = job.get("container_id")
        if not container_id:
            self.db.update_job(jid, status=JobStatus.UPLOADING,
                               upload_method=UploadMethod.HOSTED_URL)
            kwargs: dict = {"media_type": meta_type}
            kwargs["video_url" if is_video else "image_url"] = media_url
            if media_type not in _STORY_TYPES:
                kwargs["caption"] = self._caption_for(job, page)
            if media_type == MediaType.REEL:
                kwargs["share_to_feed"] = page.publishing.share_reel_to_feed
            container_id = client.create_media_container(target.ig_user_id, **kwargs)
            self.db.update_job(jid, container_id=container_id,
                               status=JobStatus.CONTAINER_CREATED)
            self.db.log_event(job_id=jid, page_id=page.page_id, event="container_create",
                              request_summary=f"{meta_type} url={media_url}",
                              response_summary=container_id)
        self._await_finished(client, container_id)
        return self._finalize_publish(job, page, target, container_id)

    def _finalize_publish(self, job: dict, page: PageConfig, target: PublishTarget,
                          container_id: str) -> PublishOutcome:
        jid = job["id"]
        if job.get("remote_media_id"):  # idempotent: already published
            return PublishOutcome(jid, JobStatus.PUBLISHED, self.s.mode,
                                  remote_media_id=job["remote_media_id"],
                                  container_id=container_id, message="already published")
        self.db.update_job(jid, status=JobStatus.PUBLISHING)
        media_id = target.client.publish_container(target.ig_user_id, container_id)
        self.db.update_job(jid, remote_media_id=media_id, status=JobStatus.PUBLISHED,
                           published_at=utcnow_iso())
        self.db.log_event(job_id=jid, page_id=page.page_id, event="publish",
                          response_summary=media_id)
        log.info("Published job %s -> media %s (%s)", jid, media_id, page.page_id)
        return PublishOutcome(jid, JobStatus.PUBLISHED, self.s.mode,
                              remote_media_id=media_id, container_id=container_id,
                              message="published")

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
                fields["upload_uri"] = None
                fields["upload_offset"] = 0
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
