"""Tests for captions and the publisher — focused on the DEFAULT resumable
(direct local-file upload) flow, plus the legacy hosted_url path."""
from __future__ import annotations

from pathlib import Path

from src.content.captions import _rotate_subset, build_caption
from src.core.enums import ContentStatus, JobStatus, Mode, UploadMethod
from src.core.settings import load_settings
from src.database import Database
from src.publishing import MockGraphClient, Publisher
from src.publishing.publisher import PublishTarget


# ---- captions --------------------------------------------------------------
def test_caption_motivational_has_cta_and_tags():
    text = build_caption({
        "content_type": "motivational",
        "caption": "Una spiegazione concreta e utile del significato della frase.",
        "call_to_action": "Qual è il tuo primo passo oggi?",
        "hashtags": ["#motivazione", "#costanza", "#crescita"], "content_hash": "h1"})
    assert "primo passo" in text and "#motivazione" in text


def test_caption_quote_has_author_attribution():
    text = build_caption({
        "content_type": "famous_quote", "caption": "Seneca invita alla pazienza.",
        "author_display_name": "Lucio Anneo Seneca", "source_work": "Lettere a Lucilio",
        "hashtags": ["#seneca"]}, content_type="famous_quote")
    assert "Lucio Anneo Seneca" in text and "Lettere a Lucilio" in text


def test_hashtag_rotation_deterministic_and_capped():
    tags = ["#1", "#2", "#3", "#4", "#5", "#6"]
    assert _rotate_subset(tags, "k", 3) == _rotate_subset(tags, "k", 3)
    assert len(_rotate_subset(tags, "k", 3)) == 3


# ---- helpers ---------------------------------------------------------------
def _setup(project_paths, mode, *, public_url=""):
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = mode
    s.publishing.public_media_base_url = public_url
    from src.accounts import load_pages
    return s, load_pages(project_paths)


def _job(db: Database, s, key, media_type="reel", *, size=4000, make_file=True):
    cid, _ = db.insert_content(dict(
        content_type="motivational", text="Un passo alla volta.",
        normalized_text="un passo alla volta", content_hash=f"c-{key}",
        caption="Una spiegazione concreta e utile del significato.", hashtags=["#a", "#b"],
        mood="calm", quality_score=0.9, status=ContentStatus.APPROVED_FOR_PUBLICATION))
    out = s.paths.stories / f"{key}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if make_file:
        out.write_bytes(b"\x00" * size)   # dummy mp4 (probe returns dur=0 -> skip dur check)
    jid, _ = db.create_job(page_id="motivational_it", content_id=cid,
                           media_type=media_type, idempotency_key=key,
                           scheduled_at=None, status=JobStatus.MEDIA_READY)
    db.update_job(jid, output_path=str(out))
    return jid, cid


def _pub(s, db, reg, mock, sleep=lambda _: None):
    return Publisher(s, db, reg, client_factory=lambda p: PublishTarget(mock, "ig"),
                     sleep=sleep)


# ---- resumable: happy paths ------------------------------------------------
def test_resumable_reel_uploads_with_caption_and_share_to_feed(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)  # no public_media_base_url set!
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "reel1", media_type="reel")
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    assert out.status == JobStatus.PUBLISHED
    creates = [c for c in mock.calls if c[0] == "create_resumable"]
    assert creates and creates[0][1] == "REELS"
    assert creates[0][2] is True                    # share_to_feed
    assert creates[0][3] and "spiegazione" in creates[0][3]  # caption present on Reel
    assert any(c[0] == "upload" for c in mock.calls)
    job = tmp_db.get_job(jid)
    assert job["upload_status"] == "completed"
    assert job["container_id"].startswith("mock_container")


def test_resumable_story_has_no_caption(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "story1", media_type="story_video")
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    assert out.status == JobStatus.PUBLISHED
    create = next(c for c in mock.calls if c[0] == "create_resumable")
    assert create[1] == "STORIES"
    assert create[2] is None      # no share_to_feed on story
    assert create[3] is None      # no caption on story


def test_resumable_no_public_url_required(tmp_db, project_paths):
    # public_media_base_url is empty and must NOT be needed for resumable.
    s, reg = _setup(project_paths, Mode.TEST, public_url="")
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "nourl")
    assert _pub(s, tmp_db, reg, mock).publish_job(jid).status == JobStatus.PUBLISHED


def test_resumable_streams_correct_file_size(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "sz", size=12345)
    _pub(s, tmp_db, reg, mock).publish_job(jid)
    cid = next(c for c in mock.containers)
    assert mock.containers[cid]["size"] == 12345   # full file streamed


# ---- resumable: idempotency & recovery -------------------------------------
def test_resumable_idempotent_no_second_container(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient()
    pub = _pub(s, tmp_db, reg, mock)
    jid, _ = _job(tmp_db, s, "idem")
    pub.publish_job(jid)
    n = len([c for c in mock.calls if c[0] == "create_resumable"])
    out2 = pub.publish_job(jid)                      # already published
    assert "already published" in out2.message
    assert len([c for c in mock.calls if c[0] == "create_resumable"]) == n


def test_resumable_resume_after_interrupted_upload(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    s.publishing.max_retries = 3
    mock = MockGraphClient(upload_partial_bytes=1500)  # file is 4000 bytes
    pub = _pub(s, tmp_db, reg, mock)
    jid, _ = _job(tmp_db, s, "resume", size=4000)
    o1 = pub.publish_job(jid)                        # interrupted mid-upload
    assert o1.status == JobStatus.RETRY_PENDING
    o2 = pub.publish_job(jid)                        # resumes from offset
    assert o2.status == JobStatus.PUBLISHED
    offsets = [c[1] for c in mock.calls if c[0] == "upload"]
    assert offsets == [0, 1500]                      # resumed from 1500, not 0
    # container was reused, not recreated
    assert len([c for c in mock.calls if c[0] == "create_resumable"]) == 1


def test_resumable_skips_upload_if_already_finished(tmp_db, project_paths):
    """Crash after upload, before publish: must not re-upload, just publish."""
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "afterupload", size=2000)
    # simulate a container already fully uploaded
    cid, uri = mock.create_resumable_container("ig", media_type="REELS")
    mock.containers[cid]["size"] = 2000
    mock.containers[cid]["transferred"] = 2000
    tmp_db.update_job(jid, container_id=cid, upload_uri=uri, upload_offset=2000)
    n_uploads = len([c for c in mock.calls if c[0] == "upload"])
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    assert out.status == JobStatus.PUBLISHED
    assert len([c for c in mock.calls if c[0] == "upload"]) == n_uploads  # no new upload


def test_resumable_container_error_recreates(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "cerr")
    # a stale container id unknown to Meta -> get_container_status returns ERROR
    tmp_db.update_job(jid, container_id="stale_container", upload_uri="https://x/stale")
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    # stale container ERROR -> cleared + recreated -> published
    assert out.status == JobStatus.PUBLISHED
    assert any(c[0] == "create_resumable" for c in mock.calls)
    assert tmp_db.get_job(jid)["container_id"].startswith("mock_container")


# ---- resumable: transient errors -------------------------------------------
def test_resumable_create_retry_then_success(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    s.publishing.max_retries = 3
    mock = MockGraphClient(create_fail_times=1)
    pub = _pub(s, tmp_db, reg, mock)
    jid, _ = _job(tmp_db, s, "cretry")
    assert pub.publish_job(jid).status == JobStatus.RETRY_PENDING
    assert pub.publish_job(jid).status == JobStatus.PUBLISHED


def test_resumable_upload_retry_then_success(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    s.publishing.max_retries = 3
    mock = MockGraphClient(upload_fail_times=1)
    pub = _pub(s, tmp_db, reg, mock)
    jid, _ = _job(tmp_db, s, "uretry")
    assert pub.publish_job(jid).status == JobStatus.RETRY_PENDING
    assert pub.publish_job(jid).status == JobStatus.PUBLISHED


def test_resumable_rate_limit_is_retryable(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient(rate_limited=True)
    jid, _ = _job(tmp_db, s, "rl")
    assert _pub(s, tmp_db, reg, mock).publish_job(jid).status == JobStatus.RETRY_PENDING


# ---- validation / account --------------------------------------------------
def test_missing_local_file_fails(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "nofile", make_file=False)
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    assert out.status == JobStatus.FAILED
    assert "mancante" in out.message.lower()


def test_personal_account_rejected(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient(account_type="personal")
    jid, _ = _job(tmp_db, s, "personal")
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    assert out.status == JobStatus.FAILED
    assert "personale" in out.message.lower()


def test_story_on_non_business_rejected(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST)
    mock = MockGraphClient(account_type="creator")
    jid, _ = _job(tmp_db, s, "storycreator", media_type="story_video")
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    assert out.status == JobStatus.FAILED
    assert "business" in out.message.lower()


# ---- dry-run & credentials -------------------------------------------------
def test_dry_run_no_network_and_idempotent(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.DRY_RUN)
    jid, _ = _job(tmp_db, s, "dry1")
    pub = Publisher(s, tmp_db, reg)  # default factory, but dry-run never uses it
    out = pub.publish_job(jid)
    assert out.status == JobStatus.PUBLISHED and out.remote_media_id.startswith("DRYRUN")
    assert "already published" in pub.publish_job(jid).message


def test_production_missing_credentials_fails(tmp_db, project_paths, monkeypatch):
    for k in ("META_ACCESS_TOKEN", "ICE_MOTIVATIONAL_IT_ACCESS_TOKEN",
              "ICE_MOTIVATIONAL_IT_IG_USER_ID"):
        monkeypatch.delenv(k, raising=False)
    s, reg = _setup(project_paths, Mode.PRODUCTION)
    jid, _ = _job(tmp_db, s, "prod1")
    out = Publisher(s, tmp_db, reg).publish_job(jid)
    assert out.status == JobStatus.FAILED and "credential" in out.message.lower()


# ---- legacy hosted_url path still works ------------------------------------
def test_hosted_url_path(tmp_db, project_paths):
    s, reg = _setup(project_paths, Mode.TEST, public_url="https://cdn.example.com/media")
    s.publishing.upload_method = UploadMethod.HOSTED_URL
    mock = MockGraphClient()
    jid, _ = _job(tmp_db, s, "hosted")
    tmp_db.update_job(jid, upload_method=UploadMethod.HOSTED_URL)
    out = _pub(s, tmp_db, reg, mock).publish_job(jid)
    assert out.status == JobStatus.PUBLISHED
    assert any(c[0] == "create" for c in mock.calls)   # hosted uses create_media_container
