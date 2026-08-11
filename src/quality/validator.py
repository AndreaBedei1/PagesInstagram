"""Measurable image validation + the deterministic auto-repair loop.

Repair order follows the spec: change text color → move text → add overlay →
change size → change wrapping → (only then) regenerate the background (the last
step is the caller's responsibility; the validator signals it by never passing).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from ..core.settings import Settings
from ..rendering.renderer import DARK, LIGHT, RenderOptions, RenderResult
from .wcag import wcag_contrast

RenderFn = Callable[[RenderOptions], RenderResult]

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # Instagram image upload ceiling (~8MB)


@dataclass
class ImageValidation:
    score: float
    passed: bool
    contrast: float
    checks: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass
class VideoValidation:
    score: float
    passed: bool
    checks: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


MAX_VIDEO_BYTES = 100 * 1024 * 1024  # conservative ceiling for feed/story video


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass(frozen=True)
class RepairStrategy:
    """One named step of the repair escalation.

    Named rather than anonymous so a failure can say *which* repair saved a
    render — or which one was never tried, which is how the truncation went
    unnoticed for as long as it did.
    """

    name: str
    build: Callable[[RenderOptions, tuple[int, int, int]], RenderOptions]


#: The escalation, in order: change the text colour, move the text, darken
#: behind it, cover the whole frame, shrink the type, force more wrapping, and
#: finally combine the two strongest. Regenerating the background is the step
#: after this list and belongs to the caller.
#:
#: The order matters and so does the length: everything below the fifth entry
#: exists for backgrounds that a stronger scrim alone cannot rescue.
REPAIR_STRATEGIES: tuple[RepairStrategy, ...] = (
    RepairStrategy("text_color", lambda b, opp: replace(b, text_color=opp)),
    RepairStrategy("vertical_upper", lambda b, opp: replace(b, vertical="upper")),
    RepairStrategy("vertical_lower", lambda b, opp: replace(b, vertical="lower")),
    RepairStrategy("scrim_medium", lambda b, opp: replace(
        b, scrim_strength=min(0.6, b.scrim_strength + 0.22))),
    RepairStrategy("scrim_strong", lambda b, opp: replace(
        b, scrim_strength=min(0.75, b.scrim_strength + 0.42))),
    RepairStrategy("full_overlay", lambda b, opp: replace(
        b, full_overlay=True, scrim_strength=0.5)),
    RepairStrategy("font_scale", lambda b, opp: replace(b, font_scale=0.85)),
    RepairStrategy("box_shrink", lambda b, opp: replace(b, box_shrink=0.85)),
    RepairStrategy("color_and_overlay", lambda b, opp: replace(
        b, text_color=opp, full_overlay=True, scrim_strength=0.52)),
)


class MediaValidator:
    def __init__(self, settings: Settings):
        self.s = settings

    # ---- image validation ------------------------------------------------
    def validate_image(self, result: RenderResult,
                       image_path: str | Path | None = None) -> ImageValidation:
        s = self.s
        checks: dict = {}
        issues: list[str] = []

        # --- contrast (hard gate) ---
        contrast = wcag_contrast(result.text_color, result.effective_bg_rgb)
        checks["contrast"] = round(contrast, 2)
        min_contrast = s.quality.min_contrast_ratio
        contrast_ok = contrast >= min_contrast
        if not contrast_ok:
            issues.append(f"contrasto insufficiente {contrast:.1f} < {min_contrast}")

        # --- min font size (hard gate) ---
        font_ok = result.font_size >= s.rendering.min_font_px
        checks["font_size"] = result.font_size
        if not font_ok:
            issues.append(f"font troppo piccolo {result.font_size} < {s.rendering.min_font_px}")

        # --- max lines (hard gate) ---
        n_lines = len(result.lines)
        lines_ok = n_lines <= s.rendering.max_lines
        checks["num_lines"] = n_lines
        if not lines_ok:
            issues.append(f"troppe righe {n_lines} > {s.rendering.max_lines}")

        # --- background complexity behind text (soft) ---
        complexity = result.text_zone_complexity
        checks["complexity"] = round(complexity, 3)
        complexity_ok = complexity <= 0.24
        if not complexity_ok:
            issues.append(f"sfondo movimentato dietro il testo ({complexity:.2f})")

        # --- file-based checks (resolution, filesize, exposure) ---
        res_ok = True
        exposure_ok = True
        if image_path and os.path.exists(image_path):
            with Image.open(image_path) as im:
                checks["resolution"] = list(im.size)
                res_ok = tuple(im.size) == tuple(result.size)
                crop = im.crop(result.box).convert("RGB").resize((48, 48))
            arr = np.asarray(crop, dtype="float32")
            lum = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2])
            dark_frac = float((lum < 12).mean())
            bright_frac = float((lum > 248).mean())
            checks["dark_frac"] = round(dark_frac, 3)
            checks["bright_frac"] = round(bright_frac, 3)
            exposure_ok = dark_frac < 0.35 and bright_frac < 0.35
            if not exposure_ok:
                issues.append("aree troppo scure o troppo chiare nella zona testo")
            size_bytes = os.path.getsize(image_path)
            checks["file_bytes"] = size_bytes
            if size_bytes >= MAX_IMAGE_BYTES:
                issues.append("file immagine troppo grande")
            if not res_ok:
                issues.append(f"risoluzione {im.size} != {result.size}")

        # --- soft sub-scores ---
        contrast_score = _clamp((contrast - 2.0) / (7.0 - 2.0))
        font_score = _clamp((result.font_size - s.rendering.min_font_px) / 40.0 + 0.5)
        lines_score = 1.0 if lines_ok else 0.4
        complexity_score = _clamp(1.0 - complexity / 0.30)
        exposure_score = 1.0 if exposure_ok else 0.4
        score = (
            0.42 * contrast_score
            + 0.16 * font_score
            + 0.12 * lines_score
            + 0.18 * complexity_score
            + 0.12 * exposure_score
        )
        # Hard gates cap the score so a failing item can never "pass" on average.
        hard_ok = contrast_ok and font_ok and lines_ok and res_ok
        passed = hard_ok and score >= s.quality.min_score
        return ImageValidation(
            score=round(score, 4), passed=passed, contrast=round(contrast, 2),
            checks=checks, issues=issues,
        )

    # ---- video validation -------------------------------------------------
    def validate_video(self, video_path: str | Path, *, aspect: str,
                       expected_duration: float | None = None,
                       require_audio: bool = True) -> VideoValidation:
        from ..video.ffmpeg import measure_loudness, probe_media

        s = self.s
        checks: dict = {}
        issues: list[str] = []
        info = probe_media(str(video_path))
        # Reel and Story are both 9:16; only the legacy feed video is 4:5.
        target = tuple(s.rendering.story_size if aspect in ("story", "reel")
                       else s.rendering.post_size)

        # duration
        dur = info.duration or 0.0
        checks["duration"] = round(dur, 2)
        dur_ok = 2.0 <= dur <= 95.0
        if expected_duration:
            dur_ok = dur_ok and abs(dur - expected_duration) <= 2.0
        if not dur_ok:
            issues.append(f"durata anomala {dur:.1f}s")

        # resolution / aspect
        res_ok = (info.width, info.height) == target
        checks["resolution"] = [info.width, info.height]
        if not res_ok:
            issues.append(f"risoluzione {info.width}x{info.height} != {target}")

        # codecs
        checks["video_codec"] = info.video_codec
        checks["audio_codec"] = info.audio_codec
        checks["audio_sr"] = info.audio_sample_rate
        vcodec_ok = (info.video_codec or "").lower() in ("h264", "avc1")
        if not vcodec_ok:
            issues.append(f"codec video {info.video_codec} non H.264")

        # audio presence + level
        audio_ok = True
        level_ok = True
        if require_audio:
            audio_ok = info.has_audio
            if not audio_ok:
                issues.append("audio assente")
            if audio_ok:
                mx, mn = measure_loudness(str(video_path))
                checks["max_volume_db"] = mx
                checks["mean_volume_db"] = mn
                if mx is not None and mx > -1.0:
                    level_ok = False
                    issues.append(f"audio troppo alto (max {mx} dB, rischio clipping)")
                if mn is not None and mn > -9.0:
                    level_ok = False
                    issues.append(f"audio troppo presente (mean {mn} dB) per uno sfondo")
                sr_ok = info.audio_sample_rate == 48000
                if not sr_ok:
                    issues.append(f"sample rate {info.audio_sample_rate} != 48000")

        # file size
        size_ok = True
        if info.size_bytes:
            checks["file_bytes"] = info.size_bytes
            size_ok = info.size_bytes < MAX_VIDEO_BYTES
            if not size_ok:
                issues.append("file video troppo grande")

        hard_ok = dur_ok and res_ok and vcodec_ok and audio_ok and size_ok
        score = (
            0.28 * (1.0 if dur_ok else 0.3)
            + 0.24 * (1.0 if res_ok else 0.0)
            + 0.18 * (1.0 if vcodec_ok else 0.0)
            + 0.15 * (1.0 if audio_ok else 0.0)
            + 0.15 * (1.0 if level_ok else 0.4)
        )
        passed = hard_ok and level_ok
        return VideoValidation(score=round(score, 4), passed=passed,
                               checks=checks, issues=issues)

    # ---- auto-repair loop -------------------------------------------------
    @property
    def repair_limit(self) -> int:
        """How many repairs the loop may attempt.

        A floor, not a ceiling. ``quality.max_repair_attempts`` was 5 while nine
        repairs were declared, so the four strongest — full overlay, font scale,
        wrapping and the combination — could never be reached. The loop simply
        stopped and kept whatever the fifth had produced.

        That is not a tuning question, it is a truncated strategy: the sequence
        escalates on purpose, and the last steps exist precisely for the cases
        the first five cannot fix. Job 180 of the real buffer scored 0.7423,
        the best of the five reachable repairs, while full overlay scored 0.9358
        on the same background.

        So configuration may ask for *more* attempts than the declared sequence;
        it cannot ask for fewer.
        """
        return max(int(self.s.quality.max_repair_attempts), len(REPAIR_STRATEGIES))

    def _repair_candidates(self, base: RenderOptions,
                           auto_color: tuple[int, int, int]) -> list[RenderOptions]:
        opposite = LIGHT if auto_color == DARK else DARK
        return [strategy.build(base, opposite) for strategy in REPAIR_STRATEGIES]

    def render_until_valid(
        self, render_fn: RenderFn, base_options: RenderOptions | None = None,
        *, trace: list[str] | None = None,
    ) -> tuple[RenderResult, ImageValidation, RenderOptions]:
        """Render, validate, and escalate through the declared repairs.

        Returns the best (result, validation, options), stopping at the first
        repair that passes. If ``validation.passed`` is False every declared
        repair has been tried and the caller should regenerate the background —
        which is now a meaningful signal, because previously it could mean
        "the loop ran out of attempts" instead.

        ``trace`` collects the names of the repairs attempted, in order. The
        diagnostics and the tests read it; nothing else depends on it.
        """
        base = base_options or RenderOptions()
        result = render_fn(base)
        val = self.validate_image(result, result.path)
        best = (result, val, base)
        if val.passed:
            return best

        opposite = LIGHT if result.text_color == DARK else DARK
        limit = self.repair_limit
        for attempts, strategy in enumerate(REPAIR_STRATEGIES):
            if attempts >= limit:
                break
            cand = strategy.build(base, opposite)
            if trace is not None:
                trace.append(strategy.name)
            r = render_fn(cand)
            v = self.validate_image(r, r.path)
            if v.score > best[1].score:
                best = (r, v, cand)
            if v.passed:
                return (r, v, cand)
        return best
