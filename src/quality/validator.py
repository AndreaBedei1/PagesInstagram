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


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


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

    # ---- auto-repair loop -------------------------------------------------
    def _repair_candidates(self, base: RenderOptions,
                           auto_color: tuple[int, int, int]) -> list[RenderOptions]:
        opposite = LIGHT if auto_color == DARK else DARK
        return [
            replace(base, text_color=opposite),                                   # 1 color
            replace(base, vertical="upper"),                                      # 2 move
            replace(base, vertical="lower"),
            replace(base, scrim_strength=min(0.6, base.scrim_strength + 0.22)),   # 3 overlay
            replace(base, scrim_strength=min(0.75, base.scrim_strength + 0.42)),
            replace(base, full_overlay=True, scrim_strength=0.5),
            replace(base, font_scale=0.85),                                       # 4 size
            replace(base, box_shrink=0.85),                                       # 5 wrapping
            replace(base, text_color=opposite, full_overlay=True,
                    scrim_strength=0.52),                                         # combo
        ]

    def render_until_valid(
        self, render_fn: RenderFn, base_options: RenderOptions | None = None
    ) -> tuple[RenderResult, ImageValidation, RenderOptions]:
        """Render, validate, and apply ordered repairs until valid or attempts run out.

        Returns the best (result, validation, options). If ``validation.passed`` is
        False the caller should regenerate the background (last-resort step).
        """
        base = base_options or RenderOptions()
        result = render_fn(base)
        val = self.validate_image(result, result.path)
        best = (result, val, base)
        if val.passed:
            return best

        attempts = 0
        for cand in self._repair_candidates(base, result.text_color):
            if attempts >= self.s.quality.max_repair_attempts:
                break
            attempts += 1
            r = render_fn(cand)
            v = self.validate_image(r, r.path)
            if v.score > best[1].score:
                best = (r, v, cand)
            if v.passed:
                return (r, v, cand)
        return best
