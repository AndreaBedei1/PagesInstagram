"""Deterministic typography rendering (post + story) over generated backgrounds."""

from .renderer import Renderer, RenderResult
from .fonts import FontResolver

__all__ = ["Renderer", "RenderResult", "FontResolver"]
