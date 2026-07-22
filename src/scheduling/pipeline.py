"""Media generation pipeline: content -> background -> render -> validate -> video.

Direct-upload architecture: the daily *main* content is a single **9:16** video
(Reel, shared to feed). The **Story** reuses the exact same 9:16 video, phrase,
author and music — so feed and story never diverge. Only publish-time metadata
differs (Reel gets a caption + share_to_feed; Story gets neither).

For each generation:
  1. generate a 9:16 background (ComfyUI, or fallback if the mode allows it),
  2. render the text and run the quality auto-repair loop,
  3. if it still fails, regenerate the background up to N times,
  4. build the 9:16 video with baked-in, mood-matched music.
In production a ComfyUI failure with fallback disabled yields ok=False -> the
worker routes the day to NEEDS_REVIEW instead of publishing degraded media.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..accounts.models import PageConfig
from ..comfyui.backgrounds import BackgroundGenerator
from ..core.enums import MediaType
from ..core.errors import ComfyUIError
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from ..database import Database
from ..music.selector import select_track
from ..quality.validator import MediaValidator
from ..rendering.renderer import RenderOptions, Renderer
from ..video.builder import VideoBuilder

log = get_logger("scheduling.pipeline")

# All daily video jobs (Reel + Story) share the single 9:16 "reel" render.
ASPECT_FOR_MEDIA = {
    MediaType.REEL: "reel",
    MediaType.STORY_VIDEO: "reel",
    MediaType.FEED_VIDEO: "feed",
    MediaType.FEED_IMAGE: "feed",
    MediaType.STORY_IMAGE: "story",
}


@dataclass
class AspectOutput:
    aspect: str
    background_path: str
    image_path: str
    video_path: str
    has_audio: bool
    passed: bool
    score: float


@dataclass
class DailyMedia:
    content_id: int
    ok: bool
    video_path: str | None = None       # the shared 9:16 video (Reel + Story)
    image_path: str | None = None
    background_path: str | None = None
    music_track_id: str | None = None
    validation_score: float = 0.0
    media_asset_id: int | None = None
    message: str = ""


@dataclass
class PipelineResult:  # legacy multi-aspect result (CLI preview)
    content_id: int
    ok: bool
    outputs: dict[str, AspectOutput] = field(default_factory=dict)
    media_asset_id: int | None = None
    music_track_id: str | None = None
    message: str = ""

    def video_for(self, media_type: str) -> str | None:
        out = self.outputs.get(ASPECT_FOR_MEDIA.get(MediaType(media_type)))
        return out.video_path if out else None


class GenerationPipeline:
    def __init__(self, settings: Settings, db: Database, *,
                 bg_generator: BackgroundGenerator | None = None,
                 renderer: Renderer | None = None,
                 validator: MediaValidator | None = None,
                 video_builder: VideoBuilder | None = None):
        self.s = settings
        self.db = db
        self.bg = bg_generator or BackgroundGenerator(settings)
        self.renderer = renderer or Renderer(settings)
        self.validator = validator or MediaValidator(settings)
        self.video = video_builder or VideoBuilder(settings)

    def _stem(self, page: PageConfig, content_id: int, aspect: str) -> str:
        return f"{page.page_id}_{content_id}_{aspect}"

    # -- one 9:16 (or legacy) render+video --------------------------------
    def _generate_one(self, page: PageConfig, content: dict, aspect: str,
                      music_path: str | None, *, try_comfyui: bool,
                      allow_fallback: bool, max_bg_regens: int = 2) -> AspectOutput:
        p = self.s.paths
        stem = self._stem(page, content["id"], aspect)
        img_dir = p.posts if aspect == "feed" else p.stories
        rres = val = None
        bgres = None
        for regen in range(max_bg_regens + 1):
            bg_path = p.backgrounds / f"{stem}_bg{regen}.png"
            bgres = self.bg.generate(
                out_path=bg_path, background_prompt=content.get("background_prompt"),
                mood=content.get("mood"), profile=page.visual.background_profile,
                aspect=aspect, allow_fallback=allow_fallback, try_comfyui=try_comfyui)
            counter = {"n": 0}

            def render_fn(opts, _bg=bgres.path, _dir=img_dir, _stem=stem, _c=counter,
                          _aspect=aspect):
                _c["n"] += 1
                out = _dir / f"{_stem}_try{_c['n']}.png"
                return self.renderer.render(
                    background_path=_bg, out_path=out, text=content["text"],
                    content_type=page.content_type, aspect=_aspect,
                    author=content.get("author_display_name") or content.get("author"),
                    show_author=page.visual.show_author,
                    show_source_work=getattr(page.visual, "show_source_work", False),
                    logo_text=(page.visual.logo_text if page.visual.logo_enabled else None),
                    options=opts)

            rres, val, _ = self.validator.render_until_valid(render_fn)
            if val.passed:
                break
            log.info("aspect=%s bg regen %s (score=%.2f)", aspect, regen, val.score)

        final_img = img_dir / f"{stem}.png"
        Path(rres.path).replace(final_img)
        for f in img_dir.glob(f"{stem}_try*.png"):
            f.unlink(missing_ok=True)

        vpath = img_dir / f"{stem}.mp4"
        vres = self.video.build(
            image_path=final_img, out_path=vpath, aspect=aspect, music_path=music_path,
            volume_db=page.music.volume_db, fade_in=page.music.fade_in_seconds,
            fade_out=page.music.fade_out_seconds)
        return AspectOutput(aspect=aspect, background_path=str(bgres.path),
                            image_path=str(final_img), video_path=str(vpath),
                            has_audio=vres.has_audio, passed=bool(val and val.passed),
                            score=float(val.score if val else 0.0))

    # -- resolve the day's music ------------------------------------------
    def _resolve_music(self, page: PageConfig, content: dict,
                       music_track_id: str | None) -> tuple[str | None, str | None]:
        if not page.music.enabled:
            return None, None                       # enabled:false -> silent video
        if music_track_id:
            track = self.db.get_track(music_track_id)
            if track and int(track.get("instagram_safe", 0)) == 1:
                return track["track_id"], track["file_path"]
        need = max(self.s.video.reel_duration_seconds, self.s.video.story_duration_seconds)
        mood = page.music.profile or content.get("mood")
        choice = select_track(self.db, mood=content.get("mood") or mood,
                              page_id=page.page_id, duration_needed=need,
                              avoid_last_n=self.s.music.avoid_reuse_last_n)
        if not choice:
            log.warning("No instagram-safe music for mood=%s (page %s); silent video",
                        content.get("mood"), page.page_id)
            return None, None
        return choice.track["track_id"], choice.track["file_path"]

    # -- MAIN: one shared 9:16 daily video --------------------------------
    def generate_daily(self, page: PageConfig, content: dict, *,
                       music_track_id: str | None = None,
                       try_comfyui: bool = True) -> DailyMedia:
        allow_fallback = self.s.comfyui_fallback_allowed()
        track_id, music_path = self._resolve_music(page, content, music_track_id)
        try:
            out = self._generate_one(page, content, "reel", music_path,
                                     try_comfyui=try_comfyui, allow_fallback=allow_fallback)
        except ComfyUIError as e:
            log.error("Background generation failed (fallback disabled): %s", e)
            return DailyMedia(content_id=content["id"], ok=False,
                              message=f"ComfyUI non disponibile e fallback vietato: {e}")

        # Record music usage ONLY if it was actually embedded (§10).
        used_track = track_id if (track_id and out.has_audio) else None
        media_id = self.db.insert_media(
            content["id"], page.page_id, background_path=out.background_path,
            story_image_path=out.image_path, post_video_path=out.video_path,
            story_video_path=out.video_path, music_path=music_path,
            render_metadata={"music_track_id": used_track, "aspect": "reel(9:16)",
                             "score": out.score, "shared_reel_and_story": True},
            validation_score=out.score)
        if used_track:
            self.db.record_music_usage(used_track, page.page_id, None)

        return DailyMedia(
            content_id=content["id"], ok=out.passed, video_path=out.video_path,
            image_path=out.image_path, background_path=out.background_path,
            music_track_id=used_track, validation_score=out.score, media_asset_id=media_id,
            message="" if out.passed else f"qualità non superata (score {out.score:.2f})")

    # -- legacy multi-aspect (CLI preview) --------------------------------
    def generate(self, page: PageConfig, content: dict, *, aspects: list[str],
                 try_comfyui: bool = True) -> PipelineResult:
        allow_fallback = self.s.comfyui_fallback_allowed() or True  # preview always allows
        track_id, music_path = self._resolve_music(page, content, None)
        res = PipelineResult(content_id=content["id"], ok=True, music_track_id=track_id)
        for aspect in aspects:
            try:
                out = self._generate_one(page, content, aspect, music_path,
                                         try_comfyui=try_comfyui, allow_fallback=True)
            except ComfyUIError as e:
                res.ok = False
                res.message = str(e)
                continue
            res.outputs[aspect] = out
            if not out.passed:
                res.ok = False
                res.message = f"aspect {aspect} score {out.score:.2f}"
        return res
