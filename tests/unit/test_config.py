"""Tests for settings and account configuration loading/validation."""
from __future__ import annotations

import pytest

from src.accounts import load_pages
from src.accounts.models import PageConfig
from src.core.enums import Mode
from src.core.errors import ConfigError
from src.core.settings import load_settings


def test_settings_load_defaults(project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    assert s.mode in set(Mode)
    assert s.timezone == "Europe/Rome"
    assert s.rendering.post_size == (1080, 1350)
    assert s.rendering.story_size == (1080, 1920)
    assert s.comfyui.url.startswith("http")


def test_env_override_mode(project_paths, monkeypatch):
    monkeypatch.setenv("ICE_MODE", "production")
    s = load_settings(project_paths, load_dotenv=False)
    assert s.mode == Mode.PRODUCTION


FIVE_PAGES = {
    "pensiero_essenziale_it", "curiosita_mondo_it", "parola_giorno_it",
    "oggi_nella_storia_it", "domanda_giorno_it",
}


def test_load_pages_exactly_five_live_pages(project_paths):
    reg = load_pages(project_paths)
    ids = set(reg.ids())
    assert ids == FIVE_PAGES
    assert len(reg.enabled()) == 5
    # the example file must be excluded by default
    assert "example_future_it" not in ids
    # archived demo pages must not be loaded
    assert "motivational_it" not in ids and "famous_quotes_it" not in ids


def test_page_author_rules(project_paths):
    reg = load_pages(project_paths)
    for page in reg.all():
        # evergreen pages are original or sourced content: never an author line
        assert page.visual.show_author is False
    assert reg.get("pensiero_essenziale_it").env_prefix() == "ICE_PENSIERO_ESSENZIALE_IT"
    assert reg.get("oggi_nella_storia_it").env_prefix() == "ICE_OGGI_NELLA_STORIA_IT"


def test_example_page_included_when_requested(project_paths):
    reg = load_pages(project_paths, include_examples=True)
    assert "example_future_it" in reg.ids()


def test_invalid_time_rejected():
    with pytest.raises(Exception):
        PageConfig(
            page_id="x", display_name="X", content_type="motivational",
            publishing={"feed_time": "99:99"},
        )


def test_invalid_page_id_rejected():
    with pytest.raises(Exception):
        PageConfig(page_id="bad id!", display_name="X", content_type="motivational")
