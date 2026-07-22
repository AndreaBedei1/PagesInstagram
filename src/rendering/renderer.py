"""Compose background + deterministic typography into feed/story images.

The renderer exposes knobs (:class:`RenderOptions`) that the quality validator
varies to auto-repair low-contrast/overflowing layouts before ever touching the
(expensive) background regeneration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..core.settings import Settings
from .fonts import FontResolver
from .layout import fit_text, wrap_text

DARK = (27, 27, 27)
LIGHT = (250, 250, 250)


@dataclass
class Template:
    """Typographic profile for a content type."""

    family: str = "sans_semibold"
    author_family: str = "sans"
    quote_marks: bool = False
    max_font: int = 116
    min_font: int = 30
    line_spacing: float = 1.22
    author_ratio: float = 0.46      # author size relative to body size
    show_author: bool = False


TEMPLATES: dict[str, Template] = {
    "motivational": Template(
        family="sans_semibold", author_family="sans", quote_marks=False,
        max_font=120, min_font=32, line_spacing=1.24, show_author=False,
    ),
    "famous_quote": Template(
        family="serif", author_family="serif_italic", quote_marks=True,
        max_font=104, min_font=30, line_spacing=1.3, author_ratio=0.5,
        show_author=True,
    ),
}


@dataclass
class RenderOptions:
    """Repair knobs used by the quality auto-fix loop."""

    text_color: tuple[int, int, int] | None = None   # None => auto by luminance
    vertical: str = "center"                          # center | upper | lower
    scrim_strength: float = 0.24
    full_overlay: bool = False
    box_shrink: float = 1.0                           # <1.0 forces more wrapping
    font_scale: float = 1.0
    shadow: bool = True


@dataclass
class RenderResult:
    path: Path
    size: tuple[int, int]
    text_color: tuple[int, int, int]
    font_size: int
    lines: list[str]
    scrim_strength: float
    box: tuple[int, int, int, int]
    background_luminance: float            # raw bg luminance (before scrim)
    effective_bg_luminance: float          # bg luminance behind text (after scrim)
    effective_bg_rgb: tuple[int, int, int]  # mean bg color behind text (for WCAG contrast)
    text_zone_complexity: float            # stddev of luminance in the text band
    options: "RenderOptions | None" = None
    metadata: dict = field(default_factory=dict)


def _cover_resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _luminance_array(img: Image.Image, box: tuple[int, int, int, int], n: int = 32):
    crop = img.crop(box).convert("RGB").resize((n, n))
    arr = np.asarray(crop, dtype="float32")
    return 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]


def region_luminance(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    return float(_luminance_array(img, box).mean()) / 255.0


def _region_complexity(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Normalized stddev of luminance in a region (0..1). High = busy background."""
    return float(_luminance_array(img, box).std()) / 255.0


def _region_mean_rgb(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    crop = img.crop(box).convert("RGB").resize((32, 32))
    arr = np.asarray(crop, dtype="float32").reshape(-1, 3).mean(axis=0)
    return int(arr[0]), int(arr[1]), int(arr[2])


class Renderer:
    def __init__(self, settings: Settings, resolver: FontResolver | None = None):
        self.settings = settings
        self.resolver = resolver or FontResolver(settings.paths.fonts)

    # -- public API --------------------------------------------------------
    def render(
        self,
        *,
        background_path: str | Path,
        out_path: str | Path,
        text: str,
        content_type: str,
        aspect: str,                       # "feed" | "story"
        author: str | None = None,
        source_work: str | None = None,
        show_author: bool | None = None,
        show_source_work: bool = False,
        logo_text: str | None = None,
        options: RenderOptions | None = None,
    ) -> RenderResult:
        opt = options or RenderOptions()
        tpl = TEMPLATES.get(content_type, TEMPLATES["motivational"])
        # Reel and Story are both 9:16; only the legacy feed image is 4:5.
        vertical = aspect in ("story", "reel")
        size = tuple(self.settings.rendering.story_size if vertical
                     else self.settings.rendering.post_size)
        W, H = size

        canvas = _cover_resize(Image.open(background_path).convert("RGB"), size).convert("RGBA")

        # --- text-safe box --------------------------------------------------
        m = int(min(W, H) * self.settings.rendering.safe_margin_ratio)
        if vertical:
            # Story/Reel: reserve top + bottom safe zones (IG UI, caption, buttons).
            top = int(H * self.settings.rendering.story_top_safe_ratio)
            bottom_ratio = self.settings.rendering.story_bottom_safe_ratio
            if aspect == "reel":
                bottom_ratio = max(bottom_ratio, 0.20)  # Reel caption/CTA area
            bottom = int(H * (1 - bottom_ratio))
        else:
            top, bottom = m, H - m
        left, right = m, W - m

        # shrink width to force more wrapping when requested
        if opt.box_shrink < 1.0:
            cx = (left + right) / 2
            half = (right - left) * opt.box_shrink / 2
            left, right = int(cx - half), int(cx + half)

        logo = logo_text
        reserve_logo = int(H * 0.055) if logo else 0
        want_author = tpl.show_author if show_author is None else show_author
        has_author = bool(want_author and author)
        reserve_author = int((bottom - top) * 0.16) if has_author else 0
        text_bottom = bottom - reserve_logo - reserve_author
        box = (left, top, right, text_bottom)

        draw = ImageDraw.Draw(canvas)
        body = ('“' + text + '”') if tpl.quote_marks else text
        fr = fit_text(
            draw, body, self.resolver, tpl.family,
            box_w=(right - left), box_h=(text_bottom - top),
            max_font=int(tpl.max_font * opt.font_scale),
            min_font=tpl.min_font,
            line_spacing=tpl.line_spacing,
            max_lines=self.settings.rendering.max_lines,
        )

        # --- colors ---------------------------------------------------------
        lum = region_luminance(canvas, box)
        if opt.text_color is not None:
            text_color = opt.text_color
        else:
            text_color = DARK if lum > 0.55 else LIGHT
        scrim_color = DARK if text_color == LIGHT else LIGHT

        # --- scrim / overlay to boost contrast -----------------------------
        canvas = self._apply_scrim(canvas, box, scrim_color, opt.scrim_strength,
                                   opt.full_overlay)
        draw = ImageDraw.Draw(canvas)
        eff_lum = region_luminance(canvas, box)
        eff_rgb = _region_mean_rgb(canvas, box)
        complexity = _region_complexity(canvas, box)

        # --- vertical placement of the text block --------------------------
        block_h = fr.text_height
        avail = text_bottom - top
        if opt.vertical == "upper":
            y = top + avail * 0.12
        elif opt.vertical == "lower":
            y = top + avail * 0.88 - block_h
        else:
            y = top + (avail - block_h) / 2
        cx = (left + right) / 2

        self._draw_lines(canvas, fr.lines, fr.font, cx, y, fr.line_h,
                         text_color, scrim_color, opt.shadow)

        # --- author ---------------------------------------------------------
        if has_author:
            a_size = max(24, int(fr.font_size * tpl.author_ratio))
            a_font = self.resolver.get(tpl.author_family, a_size)
            author_text = f"— {author}"
            ay = text_bottom + reserve_author * 0.30
            self._draw_center(canvas, author_text, a_font, cx, ay, text_color,
                              scrim_color, opt.shadow)
            if show_source_work and source_work:
                s_font = self.resolver.get(tpl.author_family, max(20, int(a_size * 0.72)))
                self._draw_center(canvas, source_work, s_font, cx,
                                  ay + a_size * 1.4, text_color, scrim_color, opt.shadow)

        # --- logo / handle --------------------------------------------------
        if logo:
            l_font = self.resolver.get("sans", max(22, int(min(W, H) * 0.022)))
            ly = H - reserve_logo * 0.72
            self._draw_center(canvas, logo, l_font, cx, ly, text_color,
                              scrim_color, shadow=False, alpha=170)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(out_path, "PNG")
        return RenderResult(
            path=out_path, size=size, text_color=text_color, font_size=fr.font_size,
            lines=fr.lines, scrim_strength=opt.scrim_strength, box=box,
            background_luminance=round(lum, 4),
            effective_bg_luminance=round(eff_lum, 4),
            effective_bg_rgb=eff_rgb,
            text_zone_complexity=round(complexity, 4),
            options=opt,
            metadata={"aspect": aspect, "content_type": content_type,
                      "has_author": has_author, "vertical": opt.vertical,
                      "num_lines": len(fr.lines)},
        )

    # -- drawing helpers ---------------------------------------------------
    def _apply_scrim(self, img: Image.Image, box, color, strength: float,
                     full: bool) -> Image.Image:
        """Feathered radial scrim (soft spotlight) centered on the text block.

        A radial alpha mask avoids the boxy edges of a rectangle and reads as an
        intentional soft glow. ``full`` applies a flat overlay across the frame
        (used as a stronger repair step by the quality validator).
        """
        if strength <= 0 and not full:
            return img
        W, H = img.size
        base = img.convert("RGBA")
        if full:
            overlay = Image.new("RGBA", (W, H),
                                (*color, int(max(0.0, min(1.0, strength)) * 255 * 0.9)))
            return Image.alpha_composite(base, overlay)
        l, t, r, b = box
        cx, cy = (l + r) / 2.0, (t + b) / 2.0
        halfw = max(1.0, (r - l) / 2.0)
        halfh = max(1.0, (b - t) / 2.0)
        yy, xx = np.ogrid[0:H, 0:W]
        dx = (xx - cx) / (halfw * 1.28)
        dy = (yy - cy) / (halfh * 1.18)
        dist = np.sqrt(dx * dx + dy * dy)
        a = np.clip(1.0 - dist, 0.0, 1.0) ** 1.4
        alpha = (a * max(0.0, min(1.0, strength)) * 255).astype("uint8")
        overlay = np.zeros((H, W, 4), dtype="uint8")
        overlay[..., 0], overlay[..., 1], overlay[..., 2] = color
        overlay[..., 3] = alpha
        return Image.alpha_composite(base, Image.fromarray(overlay, "RGBA"))

    def _draw_lines(self, img, lines, font, cx, y, line_h, color, shadow_color,
                    shadow: bool) -> None:
        draw = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            ly = y + i * line_h
            self._draw_center(img, line, font, cx, ly, color, shadow_color,
                              shadow, draw=draw)

    def _draw_center(self, img, text, font, cx, y, color, shadow_color,
                     shadow: bool, alpha: int = 255, draw=None) -> None:
        draw = draw or ImageDraw.Draw(img)
        w = draw.textlength(text, font=font)
        x = cx - w / 2
        if shadow:
            off = max(1, font.size // 36)
            draw.text((x + off, y + off), text, font=font,
                      fill=(*shadow_color, 90))
        draw.text((x, y), text, font=font, fill=(*color, alpha))
