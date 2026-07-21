"""Media generation pipeline: content -> background -> render -> validate -> video.

For each requested aspect (feed 4:5, story 9:16):
  1. generate a background (ComfyUI, or deterministic fallback),
  2. render the text and run the quality auto-repair loop,
  3. if it still fails, regenerate the background (last-resort repair) up to N times,
  4. build the video with baked-in, mood-matched music.
Persists a media_assets row and returns per-aspect outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..accounts.models import PageConfig
from ..comfyui.backgrounds import BackgroundGenerator
from ..core.enums import MediaType
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from ..database import Database
from ..music.selector import select_track
from ..quality.validator import MediaValidator
from ..rendering.renderer import RenderOptions, Renderer
from ..video.builder import VideoBuilder

log = get_logger("scheduling.pipeline")

ASPECT_FOR_MEDIA = {
    MediaType.FEED_VIDEO: "feed",
    MediaType.FEED_IMAGE: "feed",
    MediaType.STORY_VIDEO: "story",
    MediaType.STORY_IMAGE: "story",
    MediaType.REEL: "story",
}


@dataclass
class AspectOutput:
    aspect: str
    background_path: str
    image_path: str
    video_path: str
    passed: bool
    score: float


@dataclass
class PipelineResult:
    content_id: int
    ok: bool
    outputs: dict[str, AspectOutput] = field(default_factory=dict)
    media_asset_id: int | None = None
    music_track_id: str | None = None
    message: str = ""

    def video_for(self, media_type: str) -> str | None:
        aspect = ASPECT_FOR_MEDIA.get(MediaType(media_type))
        out = self.outputs.get(aspect)
        return out.video_path if out else None

    def image_for(self, media_type: str) -> str | None:
        aspect = ASPECT_FOR_MEDIA.get(MediaType(media_type))
        out = self.outputs.get(aspect)
        return out.image_path if out else None


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

    def generate(self, page: PageConfig, content: dict, *, aspects: list[str],
                 max_bg_regens: int = 2, try_comfyui: bool = True) -> PipelineResult:
        cid = content["id"]
        p = self.s.paths
        result = PipelineResult(content_id=cid, ok=True)

        # Music (shared across aspects for a coherent post/story pair).
        need = max(self.s.video.feed_duration_seconds, self.s.video.story_duration_seconds)
        choice = select_track(self.db, mood=content.get("mood"), page_id=page.page_id,
                              duration_needed=need, avoid_last_n=self.s.music.avoid_reuse_last_n)
        music_path = choice.track["file_path"] if choice else None
        result.music_track_id = choice.track["track_id"] if choice else None
        if not choice:
            log.warning("No instagram-safe music for mood=%s (page %s); video will be silent",
                        content.get("mood"), page.page_id)

        for aspect in aspects:
            stem = self._stem(page, cid, aspect)
            rres = None
            val = None
            best_opts = RenderOptions()
            for regen in range(max_bg_regens + 1):
                bg_path = p.backgrounds / f"{stem}_bg{regen}.png"
                bgres = self.bg.generate(
                    out_path=bg_path, background_prompt=content.get("background_prompt"),
                    mood=content.get("mood"), profile=page.visual.background_profile,
                    aspect=aspect, allow_fallback=True, try_comfyui=try_comfyui,
                )
                counter = {"n": 0}
                img_dir = p.posts if aspect == "feed" else p.stories

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
                        options=opts,
                    )

                rres, val, best_opts = self.validator.render_until_valid(render_fn)
                if val.passed:
                    break
                log.info("aspect=%s bg regen %s (score=%.2f)", aspect, regen, val.score)

            # Canonical image path = the chosen best render.
            final_img = p.posts / f"{stem}.png" if aspect == "feed" else p.stories / f"{stem}.png"
            Path(rres.path).replace(final_img)
            self._cleanup_tries(img_dir, stem)

            duration = (self.s.video.story_duration_seconds if aspect == "story"
                        else self.s.video.feed_duration_seconds)
            vdir = p.posts if aspect == "feed" else p.stories
            vpath = vdir / f"{stem}.mp4"
            self.video.build(
                image_path=final_img, out_path=vpath, aspect=aspect, duration=duration,
                music_path=music_path,
                volume_db=page.music.volume_db, fade_in=page.music.fade_in_seconds,
                fade_out=page.music.fade_out_seconds,
            )

            out = AspectOutput(aspect=aspect, background_path=str(bgres.path),
                               image_path=str(final_img), video_path=str(vpath),
                               passed=bool(val and val.passed),
                               score=float(val.score if val else 0.0))
            result.outputs[aspect] = out
            if not out.passed:
                result.ok = False
                result.message = f"aspect {aspect} non ha superato la qualità (score {out.score:.2f})"

        result.media_asset_id = self._persist(page, content, result)
        if result.music_track_id:
            self.db.record_music_usage(result.music_track_id, page.page_id, None)
        return result

    def _cleanup_tries(self, img_dir: Path, stem: str) -> None:
        for f in img_dir.glob(f"{stem}_try*.png"):
            try:
                f.unlink()
            except OSError:
                pass

    def _persist(self, page: PageConfig, content: dict, result: PipelineResult) -> int:
        feed = result.outputs.get("feed")
        story = result.outputs.get("story")
        scores = [o.score for o in result.outputs.values()]
        return self.db.insert_media(
            content["id"], page.page_id,
            background_path=(feed or story).background_path if result.outputs else None,
            post_image_path=feed.image_path if feed else None,
            story_image_path=story.image_path if story else None,
            post_video_path=feed.video_path if feed else None,
            story_video_path=story.video_path if story else None,
            music_path=None,
            render_metadata={"music_track_id": result.music_track_id,
                             "outputs": {a: {"score": o.score, "passed": o.passed}
                                         for a, o in result.outputs.items()}},
            validation_score=min(scores) if scores else None,
        )
