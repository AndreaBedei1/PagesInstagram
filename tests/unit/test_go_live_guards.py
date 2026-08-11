"""The guards that stand between this repository and the first real post.

Written before the fixes, against the behaviour the go-live needs rather than
the behaviour the code had. Each group below corresponds to a defect that was
reproduced first and only then repaired:

* the publisher carried its own ``3 <= dur <= 90`` while ``core.meta_api`` said
  15 minutes — two limits, one of them wrong;
* the canary could not publish at all, because publishing went through
  ``may_publish()`` and ``may_publish()`` refuses an unarmed page, while arming
  was supposed to happen only *after* a successful canary;
* nothing stopped a second canary publication.

Nothing here touches the network: the Graph client is mocked and every gate is
a pure function that takes what it needs as arguments.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.core.enums import JobStatus, MediaType, Mode
from src.core.settings import load_settings
from src.publishing import MockGraphClient, Publisher
from src.publishing.publisher import PublishTarget

ROOT = Path(__file__).resolve().parents[2]
PAGE_ID = "pensiero_essenziale_it"


# =========================================================================
# 2. The normal path stays closed on an unarmed page
# =========================================================================
def _job(db, settings, key, *, media_type=MediaType.REEL):
    from src.core.enums import ContentStatus

    cid, _ = db.insert_content(dict(
        content_type="motivational", text="Un passo alla volta.",
        normalized_text=f"un passo alla volta {key}", content_hash=f"c-{key}",
        caption="Una spiegazione concreta e utile del significato.",
        hashtags=["#a", "#b"], mood="calm", quality_score=0.9,
        status=ContentStatus.APPROVED_FOR_PUBLICATION))
    out = Path(settings.paths.stories) / f"{key}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x00" * 4000)
    jid, _ = db.create_job(page_id=PAGE_ID, content_id=cid, media_type=media_type,
                           idempotency_key=key, scheduled_at="2099-01-01T08:00:00Z",
                           status=JobStatus.MEDIA_READY)
    db.update_job(jid, output_path=str(out))
    return jid


def _settings(project_paths, mode=Mode.TEST):
    settings = load_settings(project_paths, load_dotenv=False)
    settings.mode = mode
    from src.accounts import load_pages
    return settings, load_pages(project_paths)


def test_unarmed_page_blocks_the_normal_publisher(tmp_db, project_paths):
    settings, registry = _settings(project_paths)
    mock = MockGraphClient()
    jid = _job(tmp_db, settings, "normal-unarmed")
    outcome = Publisher(settings, tmp_db, registry,
                        client_factory=lambda p: PublishTarget(mock, "ig")).publish_job(jid)
    assert outcome.status == JobStatus.NEEDS_REVIEW
    assert not mock.containers, "nessun container per una pagina disarmata"


def test_unarmed_page_blocks_the_worker(tmp_db, project_paths):
    """The worker must not have a way round the switch the publisher enforces."""
    from src.scheduling.worker import Worker

    settings, registry = _settings(project_paths)
    mock = MockGraphClient()
    jid = _job(tmp_db, settings, "worker-unarmed")
    tmp_db.update_job(jid, scheduled_at="2000-01-01T08:00:00Z")  # overdue
    worker = Worker(settings, tmp_db, registry, try_comfyui=False, plan_enabled=False,
                    cleanup_enabled=False)
    worker.publisher = Publisher(settings, tmp_db, registry,
                                 client_factory=lambda p: PublishTarget(mock, "ig"))
    worker.run_once()
    assert tmp_db.get_job(jid)["status"] != JobStatus.PUBLISHED
    assert not mock.containers


# =========================================================================
# 3. The canary: a separate, single-use path that does not arm anything
# =========================================================================
def _canary_ready(db, settings, key="canary-1"):
    """A job whose container has been created and uploaded, ready to publish."""
    from src.publishing import canary

    jid = _job(db, settings, key)
    state = canary.CanaryState(page_id=PAGE_ID, job_id=jid,
                               container_id="mock_container_1",
                               upload_uri="https://rupload.facebook.com/x/mock_container_1",
                               uploaded_at="2026-08-11T10:00:00Z")
    canary.save_state(db, state)
    return jid, state


def _allowed(db, **overrides):
    from src.publishing import canary

    kwargs = dict(page_id=PAGE_ID, confirmation=canary.CONFIRMATION,
                  interactive=True, container_status="FINISHED",
                  audit_ok=True, health_ok=True)
    kwargs.update(overrides)
    return canary.check_publish_allowed(db, **kwargs)


def test_canary_allows_exactly_the_prepared_job(tmp_db, project_paths):
    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    ok, reason = _allowed(tmp_db, job_id=jid)
    assert ok, reason


def test_canary_refuses_without_the_confirmation_word(tmp_db, project_paths):
    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    for answer in ("", "pubblica", "Pubblica", "si", "PUBBLICA "):
        ok, reason = _allowed(tmp_db, job_id=jid, confirmation=answer)
        assert not ok, f"{answer!r} non deve valere come conferma"
        assert "conferma" in reason.lower()


def test_canary_refuses_a_non_interactive_process(tmp_db, project_paths):
    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    ok, reason = _allowed(tmp_db, job_id=jid, interactive=False)
    assert not ok and "interattivo" in reason.lower()


def test_canary_refuses_a_container_that_is_not_finished(tmp_db, project_paths):
    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    for status in ("IN_PROGRESS", "ERROR", "EXPIRED", "UNKNOWN", ""):
        ok, reason = _allowed(tmp_db, job_id=jid, container_status=status)
        assert not ok and "container" in reason.lower()


def test_canary_refuses_a_different_job(tmp_db, project_paths):
    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    other = _job(tmp_db, settings, "canary-other")
    ok, reason = _allowed(tmp_db, job_id=other)
    assert not ok and str(jid) in reason


def test_canary_refuses_a_failed_media_audit_or_health_check(tmp_db, project_paths):
    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    assert not _allowed(tmp_db, job_id=jid, audit_ok=False)[0]
    assert not _allowed(tmp_db, job_id=jid, health_ok=False)[0]


def test_canary_refuses_an_already_published_job(tmp_db, project_paths):
    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    tmp_db.update_job(jid, status=JobStatus.PUBLISHED, remote_media_id="17999")
    ok, reason = _allowed(tmp_db, job_id=jid)
    assert not ok and "pubblicat" in reason.lower()


def test_canary_publishes_once_and_never_again(tmp_db, project_paths):
    """One media, one publish call, and the second attempt is refused."""
    from src.publishing import canary

    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    mock = MockGraphClient()
    mock.containers["mock_container_1"] = {"polls": 0, "media_type": "REELS",
                                           "transferred": 4000, "size": 4000}
    target = PublishTarget(mock, "ig")

    outcome = canary.publish_canary(tmp_db, target=target, page_id=PAGE_ID, job_id=jid,
                                    confirmation=canary.CONFIRMATION, interactive=True,
                                    audit_ok=True, health_ok=True)
    assert outcome.published and outcome.media_id
    assert len([c for c in mock.calls if c[0] == "publish"]) == 1

    second = canary.publish_canary(tmp_db, target=target, page_id=PAGE_ID, job_id=jid,
                                   confirmation=canary.CONFIRMATION, interactive=True,
                                   audit_ok=True, health_ok=True)
    assert not second.published
    assert len([c for c in mock.calls if c[0] == "publish"]) == 1, (
        "il canary non deve poter pubblicare un secondo media")


def test_canary_does_not_arm_the_page(tmp_db, project_paths):
    from src.publishing import canary
    from src.publishing.arming import is_armed

    settings, _ = _settings(project_paths)
    jid, _ = _canary_ready(tmp_db, settings)
    mock = MockGraphClient()
    mock.containers["mock_container_1"] = {"polls": 0, "media_type": "REELS",
                                           "transferred": 4000, "size": 4000}
    canary.publish_canary(tmp_db, target=PublishTarget(mock, "ig"), page_id=PAGE_ID,
                          job_id=jid, confirmation=canary.CONFIRMATION, interactive=True,
                          audit_ok=True, health_ok=True)
    assert not is_armed(tmp_db, PAGE_ID), "il canary non arma nulla"


def test_the_canary_bypass_is_not_reachable_from_the_normal_path():
    """Grep-level, deliberately: the point is that nothing else may import it."""
    importers = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if path.name in ("canary.py", "__init__.py") or "cli" in path.parts:
            continue
        if re.search(r"\bfrom\s+\.*[\w.]*canary\b|\bimport\s+[\w.]*canary\b",
                     path.read_text(encoding="utf-8")):
            importers.append(str(path.relative_to(ROOT)))
    assert not importers, (
        f"il bypass del canary non deve essere raggiungibile da: {importers}")


def test_canary_selects_a_future_job_never_an_expired_one(tmp_db, project_paths):
    from src.publishing import canary

    settings, _ = _settings(project_paths)
    past = _job(tmp_db, settings, "past")
    tmp_db.update_job(past, scheduled_at="2020-01-01T08:00:00Z")
    future = _job(tmp_db, settings, "future")
    tmp_db.update_job(future, scheduled_at="2099-06-01T08:00:00Z")
    later = _job(tmp_db, settings, "later")
    tmp_db.update_job(later, scheduled_at="2099-07-01T08:00:00Z")

    chosen = canary.select_job(tmp_db, PAGE_ID)
    assert chosen is not None and chosen["id"] == future
