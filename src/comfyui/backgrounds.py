"""High-level background generation.

Maps a content's mood + the page's visual profile to an SD1.5 prompt, calls
ComfyUI, and saves the image. When ComfyUI is unreachable (no GPU / CI), it
produces a deterministic, tasteful Pillow gradient so the whole pipeline still
runs end-to-end. The chosen source is recorded in the result metadata.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ..core.errors import ComfyUIError
from ..core.logging_setup import get_logger
from ..core.settings import Settings
from .client import ComfyUIClient
from .workflow import build_graph, family_dims

log = get_logger("comfyui.backgrounds")

# Legacy SD1.5 resolutions, kept for the explicit development fallback. The
# active resolution comes from ``workflow.family_dims`` (SDXL buckets by default).
DIMS = {"feed": (768, 960), "story": (576, 1024), "reel": (576, 1024)}

#: Never let the model draw glyphs — all text is added afterwards with Pillow.
NEGATIVE = (
    "text, words, letters, lettering, typography, numbers, handwriting, "
    "watermark, signature, logo, brand, caption, subtitles, label, poster, "
    "frame, border, ui, interface, low quality, jpeg artifacts, distorted, "
    "deformed, faces, people, hands, cluttered, busy, noisy, oversaturated, "
    "harsh contrast, high detail in the center"
)

# background_profile -> style keywords appended to the prompt
PROFILES = {
    # -- the five evergreen pages ---------------------------------------
    "philosophy_warm_minimal": (
        "minimal warm abstract composition, soft sand beige and cream, gentle "
        "diffused light, matte paper grain, wide calm empty center, "
        "understated contemplative mood"
    ),
    "world_deep_geographic": (
        "abstract illustrative interpretation, deep teal and slate blue, "
        "stylised topographic and cartographic shapes, soft atmospheric depth, "
        "quiet uncluttered middle band, editorial poster feel, not photographic"
    ),
    "word_editorial_paper": (
        "aged editorial paper texture, warm ivory and faint sepia, subtle "
        "printing plate grain, soft vignette, letterpress stock feel, "
        "very calm empty center, refined lexicographic mood"
    ),
    "history_archival": (
        "archival abstract texture, muted amber and desaturated umber, faded "
        "parchment and soft film grain, quiet layered depth, restrained "
        "documentary mood, no recognisable place, calm empty center"
    ),
    "question_dark_minimal": (
        "very dark minimal background, deep charcoal and near-black, single "
        "soft distant glow, smooth low-contrast gradient, almost empty frame, "
        "quiet introspective mood"
    ),
    # -- generic profiles (legacy pages / custom pages) ------------------
    "neutral_soft": "soft neutral tones, minimal abstract, gentle gradient, subtle grain, airy",
    "elegant_neutral": "elegant editorial, muted refined palette, subtle paper texture, sophisticated",
    "minimal_warm": "minimal warm tones, soft beige and cream, cozy, understated",
    "soft_pastel": "soft pastel palette, dreamy, delicate, low contrast",
    "calm_paper": "calm paper texture, matte, organic fibers, muted",
    "blurred_landscape": "softly blurred distant landscape, bokeh, atmospheric, dreamy depth",
    "abstract_neutral": "abstract neutral shapes, smooth gradients, quiet composition",
    "editorial_elegant": "editorial elegant, fine art, restrained palette, gallery feel",
    "soft_light_gradient": "soft light gradient, luminous, gentle glow, clean",
}

#: Fallback gradient palette per profile (used only when fallback is allowed).
PROFILE_PALETTE = {
    "philosophy_warm_minimal": ("#f6efe6", "#e2d2be"),
    "world_deep_geographic": ("#20343f", "#0d1a22"),
    "word_editorial_paper": ("#f7f2e6", "#e6dcc6"),
    "history_archival": ("#efe3d2", "#c9b393"),
    "question_dark_minimal": ("#23262b", "#0e1013"),
}

# mood -> a short tone cue + a light gradient palette for the fallback
MOOD_TONE = {
    "calm": ("calm serene cool tones", ("#eef2f5", "#d5e0ea")),
    "reflective": ("reflective muted twilight", ("#e9e6f0", "#cbc4dc")),
    "hopeful": ("hopeful warm sunrise glow", ("#fdf3e7", "#f4d5b0")),
    "peaceful": ("peaceful soft green", ("#eaf3ee", "#cbe0d3")),
    "inspirational": ("inspirational airy blue", ("#eef1f7", "#ccd6ea")),
    "determined": ("grounded warm taupe", ("#f2ede8", "#d5c6b6")),
    "serene": ("serene pale teal", ("#eaf1f4", "#cfdee5")),
    "resilient": ("earthy resilient tone", ("#f0ece9", "#d3c5ba")),
    "grateful": ("warm grateful peach", ("#fbf1ea", "#eed2be")),
    "courageous": ("bold warm sand", ("#f3ece9", "#dbc4b9")),
    "focused": ("neutral focused grey", ("#edeff2", "#d0d5dc")),
    "tender": ("tender soft rose", ("#fbeef2", "#ead0da")),
}


@dataclass
class BackgroundResult:
    path: Path
    width: int
    height: int
    seed: int
    prompt: str
    negative: str
    source: str  # "comfyui" | "fallback"
    metadata: dict = field(default_factory=dict)


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def derive_seed(key: str, attempt: int = 0) -> int:
    h = hashlib.sha256(f"{key}:{attempt}".encode()).hexdigest()
    return int(h[:12], 16) % (2**31)


class BackgroundGenerator:
    def __init__(self, settings: Settings, client: ComfyUIClient | None = None):
        self.settings = settings
        self.client = client or ComfyUIClient(
            settings.comfyui.url,
            request_timeout=settings.comfyui.request_timeout_seconds,
            poll_interval=settings.comfyui.poll_interval_seconds,
        )

    # -- prompt ------------------------------------------------------------
    def build_prompt(self, background_prompt: str | None, mood: str | None,
                     profile: str | None) -> str:
        parts: list[str] = []
        if background_prompt:
            parts.append(background_prompt.strip())
        tone = MOOD_TONE.get(mood or "", ("", None))[0]
        if tone:
            parts.append(tone)
        style = PROFILES.get(profile or "neutral_soft", PROFILES["neutral_soft"])
        parts.append(style)
        # Vertical, typography-friendly composition; never any glyphs.
        parts.append(
            "vertical 9:16 composition, clean uncluttered area in the middle for "
            "text overlay, low visual complexity behind the text, absolutely no "
            "text, no letters, no watermark, high quality, soft focus"
        )
        return ", ".join(p for p in parts if p)

    # -- availability ------------------------------------------------------
    def ensure_comfyui(self) -> bool:
        c = self.settings.comfyui
        try:
            return self.client.ensure_running(c.launch_command if c.auto_start else None,
                                               c.startup_timeout_seconds)
        except ComfyUIError:
            return False

    # -- main --------------------------------------------------------------
    def generate(
        self,
        *,
        out_path: str | Path,
        background_prompt: str | None,
        mood: str | None,
        profile: str | None,
        aspect: str = "feed",
        seed: int | None = None,
        allow_fallback: bool = True,
        try_comfyui: bool = True,
    ) -> BackgroundResult:
        c = self.settings.comfyui
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = family_dims(c.model_family, aspect)
        prompt = self.build_prompt(background_prompt, mood, profile)
        key = f"{background_prompt}|{mood}|{profile}|{aspect}"

        if try_comfyui and self.client.is_ready():
            for attempt in range(c.max_retries):
                # A caller-supplied seed is authoritative on the first attempt;
                # retries perturb it so a bad sample is not simply repeated.
                s = (seed + attempt if seed is not None
                     else derive_seed(key, attempt)) % (2**31)
                graph = build_graph(
                    c.model_family,
                    prompt=prompt, negative=NEGATIVE, width=width, height=height,
                    seed=s, checkpoint=c.checkpoint,
                    steps=c.steps, cfg=c.cfg, sampler_name=c.sampler_name,
                    scheduler=c.scheduler,
                )
                try:
                    data = self.client.generate(
                        graph, timeout=c.request_timeout_seconds
                    )
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    img.save(out_path, "PNG")
                    log.info("Background via ComfyUI %s (seed=%s attempt=%s) -> %s",
                             c.model_family, s, attempt, out_path.name)
                    return BackgroundResult(
                        path=out_path, width=img.width, height=img.height, seed=s,
                        prompt=prompt, negative=NEGATIVE, source="comfyui",
                        metadata={"attempt": attempt, "checkpoint": c.checkpoint,
                                  "model_family": c.model_family,
                                  "profile": profile},
                    )
                except ComfyUIError as e:
                    log.warning("ComfyUI attempt %s failed: %s", attempt, e)
            if not allow_fallback:
                raise ComfyUIError(
                    f"generazione ComfyUI fallita dopo {c.max_retries} tentativi "
                    f"(checkpoint={c.checkpoint})")

        if not allow_fallback:
            raise ComfyUIError(
                f"ComfyUI non raggiungibile su {c.url} e fallback disabilitato "
                f"(modalità {self.settings.mode})")

        s = seed if seed is not None else derive_seed(key, 0)
        return self._fallback(out_path, width, height, mood, prompt, s, profile)

    # -- deterministic fallback -------------------------------------------
    def _fallback(self, out_path: Path, width: int, height: int, mood: str | None,
                  prompt: str, seed: int,
                  profile: str | None = None) -> BackgroundResult:
        if profile in PROFILE_PALETTE:
            top_hex, bot_hex = PROFILE_PALETTE[profile]
        else:
            top_hex, bot_hex = MOOD_TONE.get(mood or "", ("", ("#eef2f5", "#d5e0ea")))[1]
        top, bot = _hex(top_hex), _hex(bot_hex)
        # Render at a smaller size then upscale for smooth gradient + soft grain.
        img = Image.new("RGB", (width, height))
        px = img.load()
        for y in range(height):
            t = y / max(1, height - 1)
            r = int(top[0] + (bot[0] - top[0]) * t)
            g = int(top[1] + (bot[1] - top[1]) * t)
            b = int(top[2] + (bot[2] - top[2]) * t)
            for x in range(width):
                px[x, y] = (r, g, b)
        # subtle radial light + blur for an organic, low-detail look. Dark
        # profiles get a weaker glow so they stay dark enough for light text.
        mean_lum = sum(top) / 3.0 / 255.0
        glow = 48 if mean_lum > 0.35 else 26
        overlay = Image.new("L", (width, height), 0)
        d = ImageDraw.Draw(overlay)
        cx, cy = int(width * 0.5), int(height * 0.36)
        rad = int(min(width, height) * 0.55)
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=glow)
        overlay = overlay.filter(ImageFilter.GaussianBlur(rad // 3))
        light = Image.new("RGB", (width, height), (255, 255, 255))
        img = Image.composite(light, img, overlay)
        img = img.filter(ImageFilter.GaussianBlur(1.2))
        img.save(out_path, "PNG")
        log.info("Background via deterministic fallback (mood=%s) -> %s", mood, out_path.name)
        return BackgroundResult(
            path=out_path, width=width, height=height, seed=seed, prompt=prompt,
            negative=NEGATIVE, source="fallback",
            metadata={"mood": mood, "profile": profile},
        )
