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


def test_load_pages_two_live_pages(project_paths):
    reg = load_pages(project_paths)
    ids = set(reg.ids())
    assert {"motivational_it", "famous_quotes_it"} <= ids
    # the example file must be excluded by default
    assert "example_future_it" not in ids


def test_page_author_rules(project_paths):
    reg = load_pages(project_paths)
    mot = reg.get("motivational_it")
    quo = reg.get("famous_quotes_it")
    assert mot.visual.show_author is False       # original phrases: no author
    assert quo.visual.show_author is True        # quotes: author always shown
    assert mot.env_prefix() == "ICE_MOTIVATIONAL_IT"


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
