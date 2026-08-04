"""Compose background + deterministic typography into a finished 9:16 image.

The renderer is a **block-stack engine**: a :class:`~src.rendering.templates.LayoutTemplate`
describes an ordered list of typographic blocks (eyebrow, display, rule, body,
caption…), and one binary search finds the largest *base* font size for which the
whole stack fits the safe box. Each block's own size is ``base * scale``, so the
five evergreen pages get genuinely different hierarchies from a single, testable
fitting algorithm.

:class:`RenderOptions` exposes the knobs the quality validator varies to
auto-repair low-contrast or overflowing layouts before falling back to the
(expensive) background regeneration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..core.settings import Settings
from .fonts import FontResolver
from .layout import draw_tracked, line_height, measure, wrap_tracked
from .templates import Block, LayoutTemplate, build_fields, get_template

DARK = (27, 27, 27)
LIGHT = (250, 250, 250)


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
    font_size: int                          # the *body* block size (validated)
    lines: list[str]                        # the body block's wrapped lines
    scrim_strength: float
    box: tuple[int, int, int, int]          # the measured text bounding box
    background_luminance: float             # raw bg luminance (before scrim)
    effective_bg_luminance: float           # bg luminance behind text (after scrim)
    effective_bg_rgb: tuple[int, int, int]  # mean bg color behind text (WCAG)
    text_zone_complexity: float             # stddev of luminance in the text band
    options: "RenderOptions | None" = None
    metadata: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
#  internal measurement types
# --------------------------------------------------------------------------
@dataclass
class _Item:
    block: Block
    lines: list[str]
    font: object | None
    font_size: int
    line_h: float
    height: float
    width: float
    space_before: float


@dataclass
class _Stack:
    items: list[_Item]
    base: int
    height: float
    width: float
    fits: bool


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
        aspect: str,                       # "feed" | "story" | "reel"
        author: str | None = None,
        source_work: str | None = None,
        show_author: bool | None = None,
        show_source_work: bool = False,
        logo_text: str | None = None,
        options: RenderOptions | None = None,
        fields: dict[str, str] | None = None,
        template_id: str | None = None,
    ) -> RenderResult:
        opt = options or RenderOptions()
        tpl = get_template(template_id or content_type)
        vertical = aspect in ("story", "reel")
        size = tuple(self.settings.rendering.story_size if vertical
                     else self.settings.rendering.post_size)
        W, H = size

        canvas = _cover_resize(Image.open(background_path).convert("RGB"),
                               size).convert("RGBA")

        fields = dict(fields) if fields else self._legacy_fields(
            tpl, text, author, source_work, show_author, show_source_work)
        fields.setdefault("text", text)

        left, top, right, bottom = self._safe_box(W, H, aspect, vertical, opt)
        reserve_logo = int(H * 0.055) if logo_text else 0
        bottom -= reserve_logo

        draw = ImageDraw.Draw(canvas)
        stack = self._fit_stack(draw, tpl, fields, right - left, bottom - top, opt)

        # --- vertical placement of the whole stack -------------------------
        avail = bottom - top
        anchor = opt.vertical if opt.vertical != "center" else tpl.anchor
        if anchor == "upper":
            y0 = top + avail * 0.10
        elif anchor == "lower":
            y0 = top + avail * 0.90 - stack.height
        else:
            y0 = top + (avail - stack.height) / 2
        y0 = max(top, min(y0, bottom - stack.height))

        pad = max(12, int(min(W, H) * 0.028))
        tbox = (max(0, left - pad), max(0, int(y0) - pad),
                min(W, right + pad), min(H, int(y0 + stack.height) + pad))

        # --- colours ------------------------------------------------------
        lum = region_luminance(canvas, tbox)
        text_color = opt.text_color if opt.text_color is not None else (
            DARK if lum > 0.55 else LIGHT)
        scrim_color = DARK if text_color == LIGHT else LIGHT

        canvas = self._apply_scrim(canvas, tbox, scrim_color, opt.scrim_strength,
                                   opt.full_overlay)
        eff_lum = region_luminance(canvas, tbox)
        eff_rgb = _region_mean_rgb(canvas, tbox)
        complexity = _region_complexity(canvas, tbox)

        # --- draw ----------------------------------------------------------
        cx = (left + right) / 2
        self._draw_stack(canvas, stack, cx, y0, left, right, text_color,
                         scrim_color, opt.shadow)

        if logo_text:
            l_font = self.resolver.get("sans", max(22, int(min(W, H) * 0.022)))
            ly = H - reserve_logo * 0.72
            self._draw_center(canvas, logo_text, l_font, cx, ly, text_color,
                              scrim_color, shadow=False, alpha=170)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(out_path, "PNG")

        body = self._body_item(stack, tpl)
        return RenderResult(
            path=out_path, size=size, text_color=text_color,
            font_size=body.font_size if body else stack.base,
            lines=list(body.lines) if body else [],
            scrim_strength=opt.scrim_strength, box=tbox,
            background_luminance=round(lum, 4),
            effective_bg_luminance=round(eff_lum, 4),
            effective_bg_rgb=eff_rgb,
            text_zone_complexity=round(complexity, 4),
            options=opt,
            metadata={
                "aspect": aspect, "content_type": content_type,
                "template": tpl.id, "vertical": anchor,
                "has_author": bool(fields.get("author")),
                "num_lines": len(body.lines) if body else 0,
                "base_font": stack.base,
                "blocks": [
                    {"key": it.block.key, "size": it.font_size,
                     "lines": len(it.lines)}
                    for it in stack.items if it.block.kind == "text"
                ],
                "stack_fits": stack.fits,
            },
        )

    def render_content(self, *, content: dict, page, background_path, out_path,
                       aspect: str = "reel",
                       options: RenderOptions | None = None) -> RenderResult:
        """Render straight from a ``contents`` row using the page's template."""
        template_id = page.template_id()
        fields = build_fields(template_id, content,
                              show_author=page.visual.show_author)
        return self.render(
            background_path=background_path, out_path=out_path,
            text=content.get("text") or "", content_type=page.content_type,
            aspect=aspect, template_id=template_id, fields=fields,
            logo_text=(page.visual.logo_text if page.visual.logo_enabled else None),
            options=options,
        )

    # -- layout helpers ----------------------------------------------------
    def _safe_box(self, W: int, H: int, aspect: str, vertical: bool,
                  opt: RenderOptions) -> tuple[int, int, int, int]:
        r = self.settings.rendering
        m = int(min(W, H) * r.safe_margin_ratio)
        if vertical:
            top = int(H * r.story_top_safe_ratio)
            bottom_ratio = r.story_bottom_safe_ratio
            if aspect == "reel":
                bottom_ratio = max(bottom_ratio, 0.20)   # Reel caption/CTA area
            bottom = int(H * (1 - bottom_ratio))
        else:
            top, bottom = m, H - m
        left, right = m, W - m
        if opt.box_shrink < 1.0:
            cx = (left + right) / 2
            half = (right - left) * opt.box_shrink / 2
            left, right = int(cx - half), int(cx + half)
        return left, top, right, bottom

    def _legacy_fields(self, tpl: LayoutTemplate, text: str, author: str | None,
                       source_work: str | None, show_author: bool | None,
                       show_source_work: bool) -> dict[str, str]:
        fields = {"text": text}
        want_author = (tpl.block("author") is not None
                       if show_author is None else show_author)
        if want_author and author:
            fields["author"] = f"— {author}"
            if show_source_work and source_work:
                fields["author"] += f", {source_work}"
        return fields

    def _block_text(self, block: Block, fields: dict[str, str]) -> str:
        raw = (fields.get(block.key) or "").strip()
        if not raw:
            return ""
        if block.quote_marks:
            raw = "“" + raw + "”"
        return raw.upper() if block.uppercase else raw

    def _measure_stack(self, draw, tpl: LayoutTemplate, fields: dict[str, str],
                       box_w: float, base: int) -> _Stack:
        items: list[_Item] = []
        total = 0.0
        widest = 0.0
        fits = True
        for block in tpl.blocks:
            space = base * block.space_before
            if block.kind == "rule":
                h = max(2.0, base * block.rule_thickness)
                items.append(_Item(block, [], None, 0, h, h,
                                   box_w * block.rule_width, space))
                total += space + h
                continue
            body = self._block_text(block, fields)
            if not body:
                if not block.optional:
                    raise ValueError(
                        f"template {tpl.id!r}: campo obbligatorio {block.key!r} vuoto")
                continue
            size = max(block.min_px, int(round(base * block.scale)))
            font = self.resolver.get(block.family, size)
            tracking = block.tracking * size
            lines = wrap_tracked(draw, body, font, box_w, tracking)
            if len(lines) > block.max_lines:
                fits = False
                lines = lines[: block.max_lines]
            lh = line_height(font, block.line_spacing)
            h = lh * len(lines)
            w = max((measure(draw, ln, font, tracking) for ln in lines), default=0.0)
            if w > box_w + 0.5:
                fits = False
            widest = max(widest, w)
            items.append(_Item(block, lines, font, size, lh, h, w, space))
            total += space + h
        return _Stack(items=items, base=base, height=total, width=widest, fits=fits)

    def _fit_stack(self, draw, tpl: LayoutTemplate, fields: dict[str, str],
                   box_w: float, box_h: float, opt: RenderOptions) -> _Stack:
        """Binary search the largest base size whose whole stack fits the box."""
        lo = tpl.base_min
        hi = max(tpl.base_min, int(tpl.base_max * opt.font_scale))
        best = self._measure_stack(draw, tpl, fields, box_w, lo)
        while lo <= hi:
            mid = (lo + hi) // 2
            stack = self._measure_stack(draw, tpl, fields, box_w, mid)
            if stack.fits and stack.height <= box_h:
                best = stack
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def _body_item(self, stack: _Stack, tpl: LayoutTemplate) -> _Item | None:
        for it in stack.items:
            if it.block.key == tpl.body_key and it.block.kind == "text":
                return it
        for it in stack.items:
            if it.block.kind == "text":
                return it
        return None

    # -- drawing helpers ---------------------------------------------------
    def _draw_stack(self, img, stack: _Stack, cx: float, y0: float,
                    left: int, right: int, color, shadow_color, shadow: bool) -> None:
        draw = ImageDraw.Draw(img)
        y = y0
        for item in stack.items:
            y += item.space_before
            if item.block.kind == "rule":
                half = item.width / 2
                thick = max(2, int(item.height))
                draw.rectangle([cx - half, y, cx + half, y + thick],
                               fill=(*color, item.block.alpha))
                y += item.height
                continue
            tracking = item.block.tracking * item.font_size
            for line in item.lines:
                w = measure(draw, line, item.font, tracking)
                x = cx - w / 2
                if shadow:
                    off = max(1, item.font_size // 36)
                    draw_tracked(draw, (x + off, y), line, item.font,
                                 (*shadow_color, 90), tracking)
                draw_tracked(draw, (x, y), line, item.font,
                             (*color, item.block.alpha), tracking)
                y += item.line_h

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
