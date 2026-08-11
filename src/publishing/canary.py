"""The first real post, and the circular dependency that made it impossible.

Publishing goes through ``Publisher.publish_job`` -> ``may_publish()``, and
``may_publish()`` refuses a page nobody armed. Arming is supposed to happen
*after* a page has been through a controlled first post. So:

    to publish the canary  ->  the page must be armed
    to arm the page        ->  the canary must have succeeded

The wrong fix is a general escape hatch — an ``--ignore-arming`` flag, a
settings key, anything the worker could reach. That trades a circular
dependency for a hole in the only guard standing between five accounts and an
accidental broadcast.

This module is the right shape instead: one path, so narrow that it can only do
the single thing the go-live needs. It publishes a container that already
exists, for one explicit page, one explicit job, once, after somebody typed a
word, from a terminal. It never arms anything, and it is imported by exactly
one place — the ``instagram canary-*`` commands. A test greps the tree to keep
it that way.

The state lives in the database because the interesting failure is a crash
*between* the upload and the publication: without persistence the next run
would create a second container and upload the same Reel again, and Meta would
be holding two identical pending media. With it, the next run finds the
container, says so, and offers to finish the job it started.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ..core.enums import TERMINAL_JOB_STATES, JobStatus
from ..core.errors import PublishError
from ..core.logging_setup import get_logger
from .arming import ensure_runtime_flags

log = get_logger("publishing.canary")

#: Typed by a person, compared exactly. Not "y", not "si", not lowercase.
CONFIRMATION = "PUBBLICA"

_STATE_KEY = "canary:state:"
#: Written once, forever. The canary is a one-off by definition.
_USED_KEY = "canary:published"
#: Written *before* the publish call, so a crash cannot be retried blindly.
_ATTEMPT_KEY = "canary:attempted"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class CanaryState:
    page_id: str = ""
    job_id: int | None = None
    container_id: str | None = None
    upload_uri: str | None = None
    uploaded_at: str = ""
    size_bytes: int = 0

    @property
    def ready_to_publish(self) -> bool:
        return bool(self.container_id and self.job_id is not None)


@dataclass(frozen=True)
class CanaryOutcome:
    published: bool
    reason: str
    media_id: str = ""
    container_id: str = ""
    job_id: int | None = None


# ---------------------------------------------------------------- persistence
def _read(db, key: str) -> dict | None:
    ensure_runtime_flags(db)
    row = db.conn.execute("SELECT value FROM runtime_flags WHERE key=?",
                          (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _write(db, key: str, payload: dict) -> None:
    ensure_runtime_flags(db)
    db.conn.execute(
        "INSERT INTO runtime_flags(key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (key, json.dumps(payload, ensure_ascii=False), _now()))
    db.conn.commit()


def load_state(db, page_id: str) -> CanaryState:
    data = _read(db, _STATE_KEY + page_id)
    return CanaryState(**data) if data else CanaryState(page_id=page_id)


def save_state(db, state: CanaryState) -> CanaryState:
    _write(db, _STATE_KEY + state.page_id, asdict(state))
    return state


def clear_state(db, page_id: str) -> None:
    ensure_runtime_flags(db)
    db.conn.execute("DELETE FROM runtime_flags WHERE key=?",
                    (_STATE_KEY + page_id,))
    db.conn.commit()


def published_record(db) -> dict | None:
    """The one-shot marker. Present means a canary has already gone out."""
    return _read(db, _USED_KEY)


def attempt_record(db) -> dict | None:
    return _read(db, _ATTEMPT_KEY)


# ------------------------------------------------------------- job selection
def select_job(db, page_id: str, *, now: str | None = None) -> dict | None:
    """The first job of this page still in the future, with its media on disk.

    Never a job whose slot has already passed. The buffer on this machine was
    built for a start date that is now behind us, and publishing a Reel dated
    last week as the very first post on a brand-new account is not the first
    impression anyone wants.
    """
    from pathlib import Path

    ensure_runtime_flags(db)
    cutoff = now or _now()
    placeholders = ",".join("?" * len(TERMINAL_JOB_STATES))
    rows = db.conn.execute(
        f"SELECT id FROM publication_jobs WHERE page_id=? AND scheduled_at > ? "
        f"AND output_path IS NOT NULL AND status NOT IN ({placeholders}) "
        f"ORDER BY scheduled_at ASC",
        (page_id, cutoff, *sorted(TERMINAL_JOB_STATES))).fetchall()
    for row in rows:
        job = db.get_job(row[0])
        if job and job.get("output_path") and Path(job["output_path"]).exists():
            return job
    return None


# -------------------------------------------------------------------- the gate
def check_publish_allowed(db, *, page_id: str, job_id: int, confirmation: str,
                          interactive: bool, container_status: str,
                          audit_ok: bool, health_ok: bool) -> tuple[bool, str]:
    """Every condition, in one pure function, before anything reaches the network.

    Deliberately takes the container status, the audit verdict and the health
    verdict as arguments rather than fetching them: the caller does the I/O, and
    this decides. That is what makes the decision testable without a network and
    without a way to accidentally skip a check.
    """
    used = published_record(db)
    if used:
        return False, (f"il canary è già stato eseguito il "
                       f"{used.get('published_at', '?')} (media "
                       f"{used.get('media_id', '?')}): è per definizione una "
                       f"sola volta")
    attempted = attempt_record(db)
    if attempted and attempted.get("job_id") != job_id:
        return False, (f"un tentativo di canary è già registrato sul job "
                       f"{attempted.get('job_id')}: verifica sull'app prima di "
                       f"riprovare")

    if not interactive:
        return False, ("il canary richiede un processo interattivo: nessuna "
                       "automazione può pubblicare il primo post")
    if confirmation != CONFIRMATION:
        return False, (f"conferma assente o errata: serve esattamente "
                       f"{CONFIRMATION!r}")

    state = load_state(db, page_id)
    if not state.ready_to_publish:
        return False, ("nessun container caricato per questa pagina: esegui "
                       "prima go_live_canary.ps1 -UploadOnly")
    if state.job_id != job_id:
        return False, (f"il container caricato appartiene al job "
                       f"{state.job_id}, non al job {job_id}")

    job = db.get_job(job_id)
    if job is None:
        return False, f"job {job_id} inesistente"
    if job.get("page_id") != page_id:
        return False, f"il job {job_id} non appartiene alla pagina {page_id!r}"
    if job.get("status") == JobStatus.PUBLISHED or job.get("remote_media_id"):
        return False, f"il job {job_id} risulta già pubblicato"

    if container_status != "FINISHED":
        return False, (f"container in stato {container_status or 'sconosciuto'!r}: "
                       f"pubblico solo un container FINISHED")
    if not audit_ok:
        return False, "il media non rispetta le specifiche Meta"
    if not health_ok:
        return False, "il health check dell'account non è verde"
    return True, "tutte le condizioni del canary sono soddisfatte"


# -------------------------------------------------------------------- actions
def prepare_upload(db, *, target, page, job: dict,
                   caption: str | None, share_to_feed: bool | None,
                   poll=None) -> CanaryState:
    """Create the container and upload the bytes. Never publishes.

    Reuses whatever a previous run left behind: an interrupted upload resumes
    from the remote offset instead of starting a second container for the same
    file.
    """
    from pathlib import Path

    client = target.client
    path = Path(job["output_path"])
    size = path.stat().st_size
    state = load_state(db, page.page_id)

    if state.container_id and state.job_id == job["id"]:
        log.info("canary: riuso del container %s", state.container_id)
    else:
        cid, uri = client.create_resumable_container(
            target.ig_user_id, media_type="REELS", caption=caption,
            share_to_feed=share_to_feed)
        state = CanaryState(page_id=page.page_id, job_id=job["id"],
                            container_id=cid, upload_uri=uri, size_bytes=size)
        save_state(db, state)
        db.update_job(job["id"], container_id=cid, upload_uri=uri,
                      status=JobStatus.CONTAINER_CREATED)

    offset = 0
    try:
        status = client.get_upload_status(state.container_id)
        offset = int(status.get("bytes_transferred") or 0)
    except PublishError:
        offset = 0
    if offset < size:
        client.upload_video_file(state.upload_uri, str(path), offset=offset)

    if poll is not None:
        poll(state.container_id)
    state = CanaryState(**{**asdict(state), "uploaded_at": _now(),
                           "size_bytes": size})
    save_state(db, state)
    db.log_event(job_id=job["id"], page_id=page.page_id, event="canary_upload",
                 request_summary=f"size={size}", response_summary=state.container_id)
    return state


def publish_canary(db, *, target, page_id: str, job_id: int, confirmation: str,
                   interactive: bool, audit_ok: bool,
                   health_ok: bool) -> CanaryOutcome:
    """One ``media_publish`` call, or none at all.

    The attempt is recorded *before* the call. If the process dies between the
    request and the response the marker is already there, so the next run
    refuses instead of publishing a second copy of a post that may well be live.
    """
    state = load_state(db, page_id)
    container_status = ""
    if state.container_id:
        try:
            container_status = str(
                target.client.get_upload_status(state.container_id).get(
                    "status_code") or "")
        except PublishError as e:
            container_status = f"non verificabile ({e})"

    allowed, reason = check_publish_allowed(
        db, page_id=page_id, job_id=job_id, confirmation=confirmation,
        interactive=interactive, container_status=container_status,
        audit_ok=audit_ok, health_ok=health_ok)
    if not allowed:
        log.warning("canary rifiutato: %s", reason)
        return CanaryOutcome(False, reason, container_id=state.container_id or "",
                             job_id=job_id)

    _write(db, _ATTEMPT_KEY, {"page_id": page_id, "job_id": job_id,
                              "container_id": state.container_id,
                              "attempted_at": _now()})
    media_id = target.client.publish_container(target.ig_user_id, state.container_id)
    _write(db, _USED_KEY, {"page_id": page_id, "job_id": job_id,
                           "container_id": state.container_id,
                           "media_id": media_id, "published_at": _now()})
    db.update_job(job_id, status=JobStatus.PUBLISHED, remote_media_id=media_id,
                  published_at=_now())
    db.log_event(job_id=job_id, page_id=page_id, event="canary_publish",
                 response_summary=media_id)
    log.info("canary pubblicato: media %s (pagina %s NON armata)", media_id, page_id)
    return CanaryOutcome(True, "pubblicato una sola volta", media_id=media_id,
                         container_id=state.container_id or "", job_id=job_id)
