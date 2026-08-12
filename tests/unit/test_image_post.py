"""The image post: a 4:5 still, and nothing that needs a codec.

These pages carry writing, so the format is a still. That is an editorial
decision, but it also removes a whole class of defect the words never needed:
an eight-second clip requires an audio track, a silent AAC stream is still an
audio stream, and the reverb that prompted this migration lived in a component
a still does not have.

So most of what follows checks absences — no ffmpeg, no music, no video_url —
because "we stopped doing that" is only true if the code path cannot.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.enums import IMAGE_TYPES, META_MEDIA_TYPE, MediaType

PAGE_ID = "pensiero_essenziale_it"


# ---- the model ------------------------------------------------------------
def test_the_image_post_is_a_first_class_media_type():
    assert MediaType.FEED_IMAGE in IMAGE_TYPES
    assert META_MEDIA_TYPE[MediaType.FEED_IMAGE] == "IMAGE"
    assert MediaType.REEL not in IMAGE_TYPES


def test_every_page_now_plans_an_image_post(project_paths):
    from src.accounts import load_pages
    from src.scheduling.planner import _media_types

    for page in load_pages(project_paths).all():
        types = _media_types(page)
        assert MediaType.FEED_IMAGE in types, page.page_id
        assert MediaType.REEL not in types, page.page_id
        assert MediaType.STORY_VIDEO not in types, (
            f"{page.page_id}: le Story restano disabilitate")


def test_a_page_asking_for_reels_still_gets_them(project_paths):
    """The migration is a configuration, not a removal of the video path."""
    from src.accounts import load_pages
    from src.scheduling.planner import _media_types

    page = load_pages(project_paths).get(PAGE_ID)
    page.publishing.feed_media_type = "REELS"
    assert _media_types(page) == [MediaType.REEL]


# ---- the rendering --------------------------------------------------------
def test_the_still_is_1080x1350_and_carries_no_audio(project_paths, tmp_db,
                                                     monkeypatch):
    from PIL import Image

    from src.core.settings import load_settings
    from src.scheduling import pipeline as pipeline_mod

    settings = load_settings(project_paths, load_dotenv=False)
    from src.accounts import load_pages
    page = load_pages(project_paths).get(PAGE_ID)

    class NoVideoBuilder:
        def build(self, *a, **k):
            raise AssertionError("un post immagine non deve costruire video")

    from src.core.enums import ContentStatus

    cid, _ = tmp_db.insert_content(dict(
        content_type="philosophical_thought",
        text="Un pensiero che sta in una riga sola.",
        normalized_text="un pensiero che sta in una riga sola",
        content_hash="c-img-render", caption="c", hashtags=["#a"], mood="calm",
        quality_score=0.9, status=ContentStatus.APPROVED_FOR_PUBLICATION))
    p = pipeline_mod.GenerationPipeline(settings, tmp_db,
                                        video_builder=NoVideoBuilder())
    content = {"id": cid, "text": "Un pensiero che sta in una riga sola.",
               "mood": "calm", "background_prompt": "soft warm gradient",
               "caption": "c", "hashtags": ["#a"]}
    result = p.generate_daily(page, content, try_comfyui=False,
                              scheduled_date="2099-01-01", cycle_number=0)

    assert result.ok, result.message
    assert result.video_path is None, "nessun MP4"
    assert result.music_track_id is None, "nessuna traccia audio"
    assert result.image_path and result.media_path == result.image_path
    assert result.image_path.lower().endswith(".png")
    with Image.open(result.image_path) as im:
        assert im.size == (1080, 1350)


def test_media_path_prefers_the_artefact_that_exists():
    from src.scheduling.pipeline import DailyMedia

    assert DailyMedia(1, True, image_path="a.png").media_path == "a.png"
    assert DailyMedia(1, True, video_path="a.mp4").media_path == "a.mp4"
    assert DailyMedia(1, True).media_path is None


# ---- the transport --------------------------------------------------------
def test_the_tunnel_serves_a_png_as_an_image(tmp_path):
    import requests

    from src.publishing.quick_tunnel import OneFileServer, content_type_for

    png = tmp_path / "post.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096)
    assert content_type_for(png) == "image/png"

    server = OneFileServer(png).start()
    try:
        assert server.route.endswith(".png"), "il path deve dichiarare il tipo"
        r = requests.get(server.local_url, timeout=10)
        assert r.status_code == 200
        assert r.headers["Content-Type"] == "image/png"
        assert int(r.headers["Content-Length"]) == png.stat().st_size
        base = server.local_url.rsplit("/media/", 1)[0]
        for forbidden in ("/", "/.env", "/database/content.sqlite", "/stories/"):
            assert requests.get(base + forbidden, timeout=10).status_code == 404
    finally:
        server.stop()


def test_content_type_never_guesses_video_for_a_still(tmp_path):
    from src.publishing.quick_tunnel import content_type_for

    assert content_type_for("a.jpg") == "image/jpeg"
    assert content_type_for("a.jpeg") == "image/jpeg"
    assert content_type_for("a.mp4") == "video/mp4"
    assert content_type_for("a.weird") == "application/octet-stream"


# ---- the publish call -----------------------------------------------------
class SpySession:
    opened = 0
    open_now = False

    def __init__(self, media_path, paths, **kw):
        self.public_url = "https://spy.trycloudflare.com/media/abc.png"

    def __enter__(self):
        type(self).opened += 1
        type(self).open_now = True
        return self

    def __exit__(self, *exc):
        type(self).open_now = False


def _image_job(db, settings, key="img-1"):
    from src.core.enums import ContentStatus, JobStatus

    cid, _ = db.insert_content(dict(
        content_type="philosophical_thought", text="Un pensiero.",
        normalized_text=f"un pensiero {key}", content_hash=f"c-{key}",
        caption="Una spiegazione utile.", hashtags=["#a"], mood="calm",
        quality_score=0.9, status=ContentStatus.APPROVED_FOR_PUBLICATION))
    out = Path(settings.paths.posts) / f"{key}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048)
    jid, _ = db.create_job(page_id=PAGE_ID, content_id=cid,
                           media_type=MediaType.FEED_IMAGE, idempotency_key=key,
                           scheduled_at="2099-01-01T08:00:00Z",
                           status=JobStatus.MEDIA_READY)
    db.update_job(jid, output_path=str(out), upload_method="hosted_url")
    return jid


@pytest.fixture
def image_settings(project_paths):
    from src.core.enums import Mode
    from src.core.settings import load_settings

    s = load_settings(project_paths, load_dotenv=False)
    s.mode = Mode.TEST
    s.publishing.upload_method = "hosted_url"
    s.publishing.hosted_url_provider = "cloudflare_quick_tunnel"
    return s


def _publisher(settings, db, monkeypatch, mock, *, armed=True):
    from src.accounts import load_pages
    from src.publishing import Publisher
    from src.publishing import quick_tunnel as qt_mod
    from src.publishing.arming import set_armed
    from src.publishing.publisher import PublishTarget

    monkeypatch.setattr(qt_mod, "TunnelSession", SpySession)
    registry = load_pages(settings.paths)
    if armed:
        for page in registry.all():
            set_armed(db, page.page_id, True)
    return Publisher(settings, db, registry,
                     client_factory=lambda p: PublishTarget(mock, "ig"),
                     sleep=lambda _s: None)


def test_an_image_post_is_created_with_image_url_and_no_video_parameters(
        tmp_db, image_settings, monkeypatch):
    from src.publishing import MockGraphClient

    SpySession.opened = 0
    mock = MockGraphClient()
    jid = _image_job(tmp_db, image_settings)
    outcome = _publisher(image_settings, tmp_db, monkeypatch, mock).publish_job(jid)

    assert outcome.status == "PUBLISHED"
    create = next(c for c in mock.calls if c[0] == "create")
    assert create[1] == "IMAGE", "media_type deve essere IMAGE, non REELS"
    assert create[2] == "https://spy.trycloudflare.com/media/abc.png"
    assert "share_to_feed" not in mock.last_create_extra
    assert not any(c[0] in ("create_resumable", "upload") for c in mock.calls)
    assert len([c for c in mock.calls if c[0] == "publish"]) == 1


def test_a_still_is_never_probed_for_duration_or_codec(tmp_db, image_settings,
                                                       monkeypatch):
    """The video probe would answer nonsense about a PNG, so it is not called."""
    from src.publishing import MockGraphClient
    from src.video import ffmpeg as ffmpeg_mod

    def explode(*a, **k):
        raise AssertionError("nessun probe video su un post immagine")

    monkeypatch.setattr(ffmpeg_mod, "probe_media", explode)
    SpySession.opened = 0
    mock = MockGraphClient()
    jid = _image_job(tmp_db, image_settings, key="img-noprobe")
    outcome = _publisher(image_settings, tmp_db, monkeypatch, mock).publish_job(jid)
    assert outcome.status == "PUBLISHED"


def test_a_second_publish_is_refused(tmp_db, image_settings, monkeypatch):
    from src.publishing import MockGraphClient

    mock = MockGraphClient()
    jid = _image_job(tmp_db, image_settings, key="img-idem")
    publisher = _publisher(image_settings, tmp_db, monkeypatch, mock)
    publisher.publish_job(jid)
    publisher.publish_job(jid)
    assert len([c for c in mock.calls if c[0] == "publish"]) == 1


def test_an_unarmed_page_publishes_no_image_either(tmp_db, image_settings,
                                                   monkeypatch):
    from src.core.enums import JobStatus
    from src.publishing import MockGraphClient

    SpySession.opened = 0
    mock = MockGraphClient()
    jid = _image_job(tmp_db, image_settings, key="img-unarmed")
    outcome = _publisher(image_settings, tmp_db, monkeypatch, mock,
                         armed=False).publish_job(jid)
    assert outcome.status == JobStatus.NEEDS_REVIEW
    assert SpySession.opened == 0 and not mock.calls


def test_a_wrong_file_kind_is_refused_before_the_tunnel(tmp_db, image_settings,
                                                        tmp_path):
    """An MP4 where a still belongs: refused here, not by Meta."""
    from src.accounts import load_pages
    from src.core.errors import PublishError
    from src.publishing import Publisher

    wrong = tmp_path / "qualcosa.mp4"
    wrong.write_bytes(b"\x00" * 1024)
    publisher = Publisher(image_settings, tmp_db, load_pages(image_settings.paths))
    with pytest.raises(PublishError) as e:
        publisher._validate_media_file(str(wrong), MediaType.FEED_IMAGE)
    assert e.value.code == "FILE_FORMAT"

    missing = tmp_path / "assente.png"
    with pytest.raises(PublishError) as e:
        publisher._validate_media_file(str(missing), MediaType.FEED_IMAGE)
    assert e.value.code == "FILE_MISSING"


# ---- what the migration leaves behind -------------------------------------
def test_a_reel_planned_before_the_migration_is_retired(tmp_db, project_paths):
    """Two jobs in one slot means two posts a day; the planner closes one.

    The idempotency key carries the media type, so switching feed_media_type
    plans a new job *beside* the old one instead of rewriting it. The buffer
    really did end up holding a Reel and an image post at the same minute.
    """
    from src.accounts import load_pages
    from src.core.enums import JobStatus
    from src.core.settings import load_settings
    from src.scheduling.planner import plan_jobs

    registry = load_pages(project_paths)
    page = registry.get(PAGE_ID)
    page.publishing.feed_media_type = "REELS"
    settings = load_settings(project_paths, load_dotenv=False)
    plan_jobs(tmp_db, registry, settings, days=3)
    reels = [j for j in tmp_db.list_jobs(page_id=PAGE_ID)
             if j["media_type"] == "reel"]
    assert reels, "servono job reel da ritirare"

    page.publishing.feed_media_type = "IMAGE"
    report = plan_jobs(tmp_db, registry, settings, days=3)
    assert report.retired >= len(reels)

    jobs = tmp_db.list_jobs(page_id=PAGE_ID)
    live = [j for j in jobs if j["status"] not in ("SKIPPED", "PUBLISHED")]
    assert {j["media_type"] for j in live} == {"feed_image"}
    slots = [j["scheduled_at"] for j in live]
    assert len(slots) == len(set(slots)), "un solo job per slot"


def test_a_published_reel_is_never_retired(tmp_db, project_paths):
    """History is not a duplicate: what went out stays as it went out."""
    from src.accounts import load_pages
    from src.core.enums import JobStatus
    from src.core.settings import load_settings
    from src.scheduling.planner import plan_jobs

    registry = load_pages(project_paths)
    page = registry.get(PAGE_ID)
    page.publishing.feed_media_type = "REELS"
    settings = load_settings(project_paths, load_dotenv=False)
    plan_jobs(tmp_db, registry, settings, days=2)
    reel = next(j for j in tmp_db.list_jobs(page_id=PAGE_ID)
                if j["media_type"] == "reel")
    tmp_db.update_job(reel["id"], status=JobStatus.PUBLISHED,
                      remote_media_id="17999")

    page.publishing.feed_media_type = "IMAGE"
    plan_jobs(tmp_db, registry, settings, days=2)
    assert tmp_db.get_job(reel["id"])["status"] == JobStatus.PUBLISHED


def test_the_day_s_media_is_regenerated_when_the_format_changed(tmp_db,
                                                                project_paths,
                                                                tmp_path):
    """An MP4 recorded for the day must not be handed to an image job."""
    from src.accounts import load_pages
    from src.scheduling.worker import Worker

    page = load_pages(project_paths).get(PAGE_ID)
    page.publishing.feed_media_type = "IMAGE"
    old = tmp_path / "ieri.mp4"
    old.write_bytes(b"\x00" * 32)
    assert not Worker._media_matches(page, str(old))
    assert Worker._media_matches(page, str(tmp_path / "oggi.png"))

    page.publishing.feed_media_type = "REELS"
    assert Worker._media_matches(page, str(old))
    assert not Worker._media_matches(page, str(tmp_path / "oggi.png"))


def test_a_late_reel_is_retired_too(tmp_db, project_paths):
    """publish_within_window would still let this morning's Reel go out tonight."""
    from src.accounts import load_pages
    from src.core.enums import JobStatus
    from src.core.settings import load_settings
    from src.scheduling.planner import plan_jobs, retire_obsolete_jobs
    from src.core.timeutils import now_utc

    registry = load_pages(project_paths)
    page = registry.get(PAGE_ID)
    page.publishing.feed_media_type = "REELS"
    settings = load_settings(project_paths, load_dotenv=False)
    plan_jobs(tmp_db, registry, settings, days=1)
    reel = next(j for j in tmp_db.list_jobs(page_id=PAGE_ID)
                if j["media_type"] == "reel")
    tmp_db.update_job(reel["id"], scheduled_at="2026-01-01T08:00:00Z",
                      status=JobStatus.MEDIA_READY)

    page.publishing.feed_media_type = "IMAGE"
    assert retire_obsolete_jobs(tmp_db, page, start=now_utc().date()) >= 1
    assert tmp_db.get_job(reel["id"])["status"] == JobStatus.SKIPPED
