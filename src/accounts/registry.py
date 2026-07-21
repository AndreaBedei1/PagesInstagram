"""Load and validate every page YAML in ``accounts/`` into a registry."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..core.errors import ConfigError
from ..core.paths import Paths
from .models import PageConfig


def _load_one(path: Path) -> PageConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path.name}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: top level must be a mapping")
    raw.setdefault("config_path", str(path))
    try:
        return PageConfig(**raw)
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"{path.name}: {e}") from e


class AccountRegistry:
    """In-memory registry of page configurations keyed by ``page_id``."""

    def __init__(self, pages: dict[str, PageConfig]):
        self._pages = pages

    def __len__(self) -> int:
        return len(self._pages)

    def __contains__(self, page_id: str) -> bool:
        return page_id in self._pages

    def get(self, page_id: str) -> PageConfig:
        try:
            return self._pages[page_id]
        except KeyError as e:
            raise ConfigError(f"Unknown page_id: {page_id!r}") from e

    def all(self) -> list[PageConfig]:
        return list(self._pages.values())

    def enabled(self) -> list[PageConfig]:
        return [p for p in self._pages.values() if p.enabled]

    def ids(self) -> list[str]:
        return list(self._pages.keys())


def load_pages(paths: Paths | None = None, *, include_examples: bool = False) -> AccountRegistry:
    """Load all ``accounts/*.yaml`` into an :class:`AccountRegistry`.

    Files named ``*example*`` are skipped unless ``include_examples`` is True.
    Duplicate ``page_id`` values raise a :class:`ConfigError`.
    """
    paths = paths or Paths.create()
    pages: dict[str, PageConfig] = {}
    if not paths.accounts.exists():
        return AccountRegistry(pages)

    for path in sorted(paths.accounts.glob("*.yaml")):
        if not include_examples and "example" in path.stem.lower():
            continue
        page = _load_one(path)
        if page.page_id in pages:
            raise ConfigError(
                f"Duplicate page_id {page.page_id!r} in {path.name} "
                f"(already defined by {pages[page.page_id].config_path})"
            )
        pages[page.page_id] = page
    return AccountRegistry(pages)
