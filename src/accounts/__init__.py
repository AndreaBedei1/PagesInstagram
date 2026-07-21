"""Multi-account configuration: one YAML per Instagram page, no code duplication."""

from .models import PageConfig
from .registry import AccountRegistry, load_pages

__all__ = ["PageConfig", "AccountRegistry", "load_pages"]
