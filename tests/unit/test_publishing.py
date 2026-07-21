"""Tests for captions, credentials, and the publisher state machine (mocked API)."""
from __future__ import annotations

from src.accounts import load_pages
from src.content.captions import _rotate_subset, build_caption
from src.core.enums import JobStatus, Mode
from src.core.settings import load_settings
from src.publishing import MockGraphClient, Publisher
from src.publishing.publisher import PublishTarget


# ---- captions --------------------------------------------------------------
def test_caption_motivational_has_cta_and_tags():
    text = build_caption({
        "content_type": "motivational",
        "caption": "Una spiegazione concreta e utile del significato della frase.",
        "call_to_action": "Qual è il tuo primo passo oggi?",
        "hashtags": ["#motivazione", "#costanza", "#crescita"],
        "content_hash": "h1",
    })
    assert "primo passo" in text
    assert "#motivazione" in text


def test_caption_quote_has_author_attribution():
    text = build_caption({
        "content_type": "famous_quote",
        "caption": "Seneca invita a rispettare i tempi delle cose.",
        "author_display_name": "Lucio Anneo Seneca",
        "source_work": "Lettere a Lucilio",
        "hashtags": ["#seneca", "#stoicismo"],
    }, content_type="famous_quote")
    assert "Lucio Anneo Seneca" in text
    assert "Lettere a Lucilio" in text


def test_hashtag_rotation_deterministic_and_capped():
    tags = ["#1", "#2", "#3", "#4", "#5", "#6"]
    a = _rotate_subset(tags, "key-a", 3)
    b = _rotate_subset(tags, "key-a", 3)
    assert a == b and len(a) == 3


# ---- publisher -------------------------------------------------------------
def _setup(db, project_paths, mode):
    s = load_settings(project_paths, load_dotenv=False)
    s.mode = mode
    s.publishing.public_media_base_url = "https://cdn.example.com/media"
    reg = load_pages(project_paths)
    return s, reg


def _job(db, s, key, media_type="feed_video"):
    from src.core.enums import ContentStatus
    cid, _ = db.insert_content(dict(
        content_type="motivational", text="Un passo alla volta.",
        normalized_text="un passo alla volta", content_hash=f"c-{key}",
        caption="Una spiegazione concreta e utile.", hashtags=["#a", "#b"],
        mood="calm", quality_score=0.9, status=ContentStatus.APPROVED_FOR_PUBLICATION))
    out = str(s.paths.generated / "posts" / f"{key}.mp4")
    jid, _ = db.create_job(page_id="motivational_it", content_id=cid,
                           media_type=media_type, idempotency_key=key,
                           scheduled_at=None, status=JobStatus.MEDIA_READY)
    db.update_job(jid, output_path=out)
    return jid


def test_dry_run_no_network_and_idempotent(tmp_db, project_paths):
    s, reg = _setup(tmp_db, project_paths, Mode.DRY_RUN)
    jid = _job(tmp_db, s, "dry1")
    pub = Publisher(s, tmp_db, reg)
    out = pub.publish_job(jid)
    assert out.status == JobStatus.PUBLISHED
    assert out.remote_media_id.startswith("DRYRUN")
    assert tmp_db.get_job(jid)["published_at"]
    # second call is idempotent, no error
    out2 = pub.publish_job(jid)
    assert "already published" in out2.message


def test_test_mode_full_flow(tmp_db, project_paths):
    s, reg = _setup(tmp_db, project_paths, Mode.TEST)
    mock = MockGraphClient()
    pub = Publisher(s, tmp_db, reg,
                    client_factory=lambda p: PublishTarget(mock, "mock_ig"),
                    sleep=lambda _: None)
    jid = _job(tmp_db, s, "test1")
    out = pub.publish_job(jid)
    assert out.status == JobStatus.PUBLISHED
    assert out.remote_media_id.startswith("mock_media")
    kinds = [c[0] for c in mock.calls]
    assert "create" in kinds and "publish" in kinds
    # idempotent: no second container created
    n_before = len([c for c in mock.calls if c[0] == "create"])
    pub.publish_job(jid)
    assert len([c for c in mock.calls if c[0] == "create"]) == n_before


def test_retry_then_success(tmp_db, project_paths):
    s, reg = _setup(tmp_db, project_paths, Mode.TEST)
    s.publishing.max_retries = 3
    mock = MockGraphClient(create_fail_times=1)
    pub = Publisher(s, tmp_db, reg,
                    client_factory=lambda p: PublishTarget(mock, "ig"),
                    sleep=lambda _: None)
    jid = _job(tmp_db, s, "retry1")
    o1 = pub.publish_job(jid)
    assert o1.status == JobStatus.RETRY_PENDING
    assert tmp_db.get_job(jid)["retry_count"] == 1
    assert tmp_db.get_job(jid)["next_retry_at"]
    o2 = pub.publish_job(jid)
    assert o2.status == JobStatus.PUBLISHED


def test_rate_limit_is_retryable(tmp_db, project_paths):
    s, reg = _setup(tmp_db, project_paths, Mode.TEST)
    mock = MockGraphClient(rate_limited=True)
    pub = Publisher(s, tmp_db, reg,
                    client_factory=lambda p: PublishTarget(mock, "ig"),
                    sleep=lambda _: None)
    jid = _job(tmp_db, s, "rl1")
    out = pub.publish_job(jid)
    assert out.status == JobStatus.RETRY_PENDING


def test_container_error_clears_container(tmp_db, project_paths):
    s, reg = _setup(tmp_db, project_paths, Mode.TEST)
    mock = MockGraphClient(container_error=True)
    pub = Publisher(s, tmp_db, reg,
                    client_factory=lambda p: PublishTarget(mock, "ig"),
                    sleep=lambda _: None)
    jid = _job(tmp_db, s, "ce1")
    out = pub.publish_job(jid)
    assert out.status == JobStatus.RETRY_PENDING
    assert tmp_db.get_job(jid)["container_id"] is None


def test_production_missing_credentials_fails(tmp_db, project_paths, monkeypatch):
    for k in ("META_ACCESS_TOKEN", "ICE_MOTIVATIONAL_IT_ACCESS_TOKEN",
              "ICE_MOTIVATIONAL_IT_IG_USER_ID"):
        monkeypatch.delenv(k, raising=False)
    s, reg = _setup(tmp_db, project_paths, Mode.PRODUCTION)
    pub = Publisher(s, tmp_db, reg)
    jid = _job(tmp_db, s, "prod1", media_type="feed_image")
    out = pub.publish_job(jid)
    assert out.status == JobStatus.FAILED
    assert "credential" in out.message.lower()
