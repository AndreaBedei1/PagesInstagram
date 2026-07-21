"""WCAG 2.x relative luminance and contrast-ratio math."""
from __future__ import annotations


def _linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def wcag_relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def wcag_contrast(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    """Contrast ratio between two colors (1.0 .. 21.0)."""
    l1 = wcag_relative_luminance(rgb1)
    l2 = wcag_relative_luminance(rgb2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)
