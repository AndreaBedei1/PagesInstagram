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
from .workflow import sd15_txt2img

log = get_logger("comfyui.backgrounds")

# Generation resolutions (SD1.5-friendly, multiples of 8); upscaled at render time.
# Reel and Story are both 9:16; feed (legacy 4:5) kept for the hosted_url path.
DIMS = {"feed": (768, 960), "story": (576, 1024), "reel": (576, 1024)}

NEGATIVE = (
    "text, words, letters, typography, watermark, signature, logo, caption, "
    "frame, border, ui, low quality, jpeg artifacts, distorted, deformed, "
    "faces, people, cluttered, busy, oversaturated, harsh contrast"
)

# background_profile -> style keywords appended to the prompt
PROFILES = {
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
        parts.append("clean background for text overlay, no text, high quality, 8k, soft focus")
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
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = DIMS.get(aspect, DIMS["feed"])
        prompt = self.build_prompt(background_prompt, mood, profile)
        key = f"{background_prompt}|{mood}|{profile}|{aspect}"

        if try_comfyui and self.client.is_ready():
            for attempt in range(self.settings.comfyui.max_retries):
                s = seed if seed is not None else derive_seed(key, attempt)
                graph = sd15_txt2img(
                    prompt=prompt, negative=NEGATIVE, width=width, height=height,
                    seed=s, checkpoint=self.settings.comfyui.checkpoint,
                )
                try:
                    data = self.client.generate(
                        graph, timeout=self.settings.comfyui.request_timeout_seconds
                    )
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    img.save(out_path, "PNG")
                    log.info("Background via ComfyUI (seed=%s attempt=%s) -> %s",
                             s, attempt, out_path.name)
                    return BackgroundResult(
                        path=out_path, width=img.width, height=img.height, seed=s,
                        prompt=prompt, negative=NEGATIVE, source="comfyui",
                        metadata={"attempt": attempt, "checkpoint": self.settings.comfyui.checkpoint},
                    )
                except ComfyUIError as e:
                    log.warning("ComfyUI attempt %s failed: %s", attempt, e)
            if not allow_fallback:
                raise ComfyUIError("ComfyUI generation failed after retries")

        if not allow_fallback:
            raise ComfyUIError("ComfyUI not available and fallback disabled")

        s = seed if seed is not None else derive_seed(key, 0)
        return self._fallback(out_path, width, height, mood, prompt, s)

    # -- deterministic fallback -------------------------------------------
    def _fallback(self, out_path: Path, width: int, height: int, mood: str | None,
                  prompt: str, seed: int) -> BackgroundResult:
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
        # subtle radial light + blur for an organic, low-detail look
        overlay = Image.new("L", (width, height), 0)
        d = ImageDraw.Draw(overlay)
        cx, cy = int(width * 0.5), int(height * 0.36)
        rad = int(min(width, height) * 0.55)
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=48)
        overlay = overlay.filter(ImageFilter.GaussianBlur(rad // 3))
        light = Image.new("RGB", (width, height), (255, 255, 255))
        img = Image.composite(light, img, overlay)
        img = img.filter(ImageFilter.GaussianBlur(1.2))
        img.save(out_path, "PNG")
        log.info("Background via deterministic fallback (mood=%s) -> %s", mood, out_path.name)
        return BackgroundResult(
            path=out_path, width=width, height=height, seed=seed, prompt=prompt,
            negative=NEGATIVE, source="fallback", metadata={"mood": mood},
        )
