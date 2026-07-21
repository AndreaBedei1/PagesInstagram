"""Text wrapping and dynamic font-size fitting."""
from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageDraw, ImageFont

from .fonts import FontResolver


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
              max_width: float) -> list[str]:
    """Greedy word wrap. Hard-breaks any single word wider than ``max_width``."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            trial = word if not current else f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                # word itself too long → hard break by characters
                if draw.textlength(word, font=font) > max_width:
                    chunk = ""
                    for ch in word:
                        if draw.textlength(chunk + ch, font=font) <= max_width:
                            chunk += ch
                        else:
                            if chunk:
                                lines.append(chunk)
                            chunk = ch
                    current = chunk
                else:
                    current = word
        if current:
            lines.append(current)
    return lines


def line_height(font: ImageFont.FreeTypeFont, spacing: float) -> float:
    ascent, descent = font.getmetrics()
    return (ascent + descent) * spacing


@dataclass
class FitResult:
    font: ImageFont.FreeTypeFont
    lines: list[str]
    font_size: int
    text_width: float
    text_height: float
    line_h: float


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    resolver: FontResolver,
    family: str,
    box_w: float,
    box_h: float,
    *,
    max_font: int = 120,
    min_font: int = 28,
    line_spacing: float = 1.2,
    max_lines: int = 8,
) -> FitResult:
    """Binary-search the largest font size whose wrapped text fits the box."""

    def build(size: int) -> FitResult:
        font = resolver.get(family, size)
        lines = wrap_text(draw, text, font, box_w)
        lh = line_height(font, line_spacing)
        th = lh * len(lines)
        tw = max((draw.textlength(ln, font=font) for ln in lines), default=0.0)
        return FitResult(font, lines, size, tw, th, lh)

    def fits(fr: FitResult) -> bool:
        return (len(fr.lines) <= max_lines
                and fr.text_height <= box_h
                and fr.text_width <= box_w + 0.5)

    lo, hi = min_font, max_font
    best = build(lo)
    while lo <= hi:
        mid = (lo + hi) // 2
        fr = build(mid)
        if fits(fr):
            best = fr
            lo = mid + 1
        else:
            hi = mid - 1
    return best
