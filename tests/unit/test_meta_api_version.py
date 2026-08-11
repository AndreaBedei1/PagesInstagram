"""The Graph API version must be identical everywhere it is written down.

The project already shipped once with four copies of the version string and two
different values (code said ``v23.0``, documentation said "Meta uses v25.0").
These tests make that class of drift a test failure instead of a surprise in
production.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.core.meta_api import (DEFAULT_GRAPH_API_VERSION,
                               GRAPH_API_VERIFIED_AT,
                               LATEST_GRAPH_API_VERSION, OFFICIAL_SOURCES,
                               REEL_MAX_SECONDS, REEL_MIN_SECONDS,
                               valid_version, version_tuple)
from src.core.settings import PublishingSettings, load_settings
from src.publishing.graph_client import GraphClient

ROOT = Path(__file__).resolve().parents[2]


def test_default_version_is_well_formed():
    assert valid_version(DEFAULT_GRAPH_API_VERSION)
    assert valid_version(LATEST_GRAPH_API_VERSION)
    # The default may lag the newest release deliberately, never lead it.
    assert version_tuple(DEFAULT_GRAPH_API_VERSION) <= version_tuple(
        LATEST_GRAPH_API_VERSION)


def test_python_settings_use_the_shared_default():
    assert PublishingSettings().graph_api_version == DEFAULT_GRAPH_API_VERSION


def test_graph_client_defaults_to_the_shared_version():
    client = GraphClient("dummy-token-not-a-secret")
    assert client.api_version == DEFAULT_GRAPH_API_VERSION
    assert client.base.endswith(f"/{DEFAULT_GRAPH_API_VERSION}")


@pytest.mark.parametrize("rel", ["config/settings.yaml",
                                 "config/settings.example.yaml"])
def test_yaml_config_matches(rel):
    data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    assert data["publishing"]["graph_api_version"] == DEFAULT_GRAPH_API_VERSION, rel


def test_env_example_matches():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^META_GRAPH_API_VERSION=(\S+)$", text, re.MULTILINE)
    assert m, "META_GRAPH_API_VERSION assente da .env.example"
    assert m.group(1) == DEFAULT_GRAPH_API_VERSION


def test_operational_doc_states_the_same_default_and_a_verification_date():
    doc = (ROOT / "docs" / "META_RESUMABLE_UPLOAD.md").read_text(encoding="utf-8")
    assert f"**Default del progetto: `{DEFAULT_GRAPH_API_VERSION}`**" in doc
    assert GRAPH_API_VERIFIED_AT.replace("-", "‑") in doc or \
        GRAPH_API_VERIFIED_AT in doc, "manca la data di verifica nel documento"
    # No stale version left behind in the doc's headline statements.
    assert "default nel progetto `v23.0`" not in doc


def test_only_official_meta_sources_are_cited():
    assert OFFICIAL_SOURCES
    for url in OFFICIAL_SOURCES:
        assert url.startswith("https://developers.facebook.com/"), url


def test_env_override_rejects_a_malformed_version(tmp_path, monkeypatch):
    monkeypatch.setenv("META_GRAPH_API_VERSION", "23")
    with pytest.raises(Exception) as exc:
        load_settings()
    assert "META_GRAPH_API_VERSION" in str(exc.value)


def test_env_override_accepts_a_well_formed_version(monkeypatch):
    monkeypatch.setenv("META_GRAPH_API_VERSION", "v26.0")
    settings = load_settings()
    assert settings.publishing.graph_api_version == "v26.0"


def test_reel_duration_limits_match_the_official_reference():
    # The official IG User /media reference: "15 mins maximum, 3 seconds minimum".
    # The project previously documented a 90-second maximum, which was wrong.
    assert REEL_MIN_SECONDS == 3
    assert REEL_MAX_SECONDS == 900


def test_generated_video_duration_is_within_the_documented_reel_limits():
    video = load_settings().video
    assert REEL_MIN_SECONDS <= video.reel_duration_seconds <= REEL_MAX_SECONDS
    assert REEL_MIN_SECONDS <= video.story_duration_seconds <= 60
