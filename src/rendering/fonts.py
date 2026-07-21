"""Font resolution with a robust fallback chain.

Priority: a font bundled in ``assets/fonts`` → a known Windows system font →
Pillow's built-in bitmap font (last resort). Users can drop a TTF/OTF into
``assets/fonts`` and reference it by family in the account YAML.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ..core.logging_setup import get_logger

log = get_logger("rendering.fonts")

_WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# Logical family -> ordered candidate filenames (bundled dir + system dir).
_CANDIDATES: dict[str, list[str]] = {
    "sans": ["Inter-Regular.ttf", "Montserrat-Regular.ttf", "segoeui.ttf", "arial.ttf", "calibri.ttf"],
    "sans_bold": ["Inter-Bold.ttf", "Montserrat-Bold.ttf", "segoeuib.ttf", "arialbd.ttf", "calibrib.ttf"],
    "sans_semibold": ["Inter-SemiBold.ttf", "Montserrat-SemiBold.ttf", "segoeuisb.ttf", "seguisb.ttf", "arialbd.ttf"],
    "serif": ["EBGaramond-Regular.ttf", "Georgia.ttf", "georgia.ttf", "times.ttf", "constan.ttf"],
    "serif_bold": ["EBGaramond-Bold.ttf", "Georgiab.ttf", "georgiab.ttf", "timesbd.ttf", "constanb.ttf"],
    "serif_italic": ["EBGaramond-Italic.ttf", "Georgiai.ttf", "georgiai.ttf", "timesi.ttf", "constani.ttf"],
}


class FontResolver:
    def __init__(self, fonts_dir: Path):
        self.fonts_dir = fonts_dir

    def _resolve_path(self, family: str) -> str | None:
        for name in _CANDIDATES.get(family, []):
            bundled = self.fonts_dir / name
            if bundled.exists():
                return str(bundled)
            system = _WIN_FONTS / name
            if system.exists():
                return str(system)
        return None

    @lru_cache(maxsize=256)
    def get(self, family: str, size: int) -> ImageFont.FreeTypeFont:
        path = self._resolve_path(family)
        if path:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                log.warning("failed to load font %s", path)
        # Fall back to the other style bucket before the bitmap default.
        if family != "sans":
            alt = self._resolve_path("sans")
            if alt:
                try:
                    return ImageFont.truetype(alt, size)
                except OSError:
                    pass
        log.warning("using Pillow default bitmap font (install a TTF in assets/fonts)")
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # older Pillow signature
            return ImageFont.load_default()

    def available(self) -> dict[str, str | None]:
        return {fam: self._resolve_path(fam) for fam in _CANDIDATES}
