"""Pydantic models describing a single Instagram page configuration.

A page is fully described by its YAML file in ``accounts/``. Adding a page means
adding one YAML file — the engine code never changes. Unknown keys are allowed
so future pages can carry extra profile hints without breaking older code.
"""
from __future__ import annotations

from datetime import time

from pydantic import BaseModel, Field, field_validator


class PublishingConfig(BaseModel):
    posts_per_day: int = 1
    publish_feed: bool = True      # main content -> Reel shared to feed
    publish_story: bool = True
    publish_reel: bool = False     # legacy alias; feed content is already a Reel
    feed_time: str = "12:30"      # HH:MM local (page timezone)
    story_time: str = "19:00"
    timezone: str = "Europe/Rome"
    missed_job_policy: str = "publish_within_window"
    missed_job_window_minutes: int = 180
    # Direct-upload architecture (defaults; can be omitted in YAML)
    upload_method: str = "resumable"        # resumable | hosted_url
    feed_media_type: str = "REELS"
    story_media_type: str = "STORIES"
    share_reel_to_feed: bool = True

    model_config = {"extra": "allow"}

    @field_validator("feed_time", "story_time")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        try:
            hh, mm = v.split(":")
            time(int(hh), int(mm))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"time must be HH:MM, got {v!r}") from e
        return v

    def feed_time_tuple(self) -> tuple[int, int]:
        hh, mm = self.feed_time.split(":")
        return int(hh), int(mm)

    def story_time_tuple(self) -> tuple[int, int]:
        hh, mm = self.story_time.split(":")
        return int(hh), int(mm)


class VisualConfig(BaseModel):
    style: str = "minimal_warm"
    show_author: bool = False
    show_source_work: bool = False
    font_family: str = "default"
    background_profile: str = "neutral_soft"
    logo_enabled: bool = False
    logo_text: str | None = None            # small page name watermark
    palette: str | None = None              # optional named palette override

    model_config = {"extra": "allow"}


class MusicConfig(BaseModel):
    enabled: bool = True
    profile: str = "calm"                   # a mood/category hint for selection
    volume_db: float | None = None          # None => use global default
    fade_in_seconds: float | None = None
    fade_out_seconds: float | None = None

    model_config = {"extra": "allow"}


class ContentConfig(BaseModel):
    source: str = "database"
    minimum_quality_score: float = 0.80
    avoid_semantic_duplicates: bool = True

    model_config = {"extra": "allow"}


class InstagramConfig(BaseModel):
    account_type: str = "business"          # business required for Stories via API
    api_flavor: str = "instagram_login"     # instagram_login | facebook_login

    model_config = {"extra": "allow"}


class PageConfig(BaseModel):
    page_id: str
    enabled: bool = True
    display_name: str
    content_type: str                       # motivational | famous_quote | ...
    language: str = "it"

    publishing: PublishingConfig = Field(default_factory=PublishingConfig)
    visual: VisualConfig = Field(default_factory=VisualConfig)
    music: MusicConfig = Field(default_factory=MusicConfig)
    content: ContentConfig = Field(default_factory=ContentConfig)
    instagram: InstagramConfig = Field(default_factory=InstagramConfig)

    # Populated by the registry, not from YAML.
    config_path: str | None = None

    model_config = {"extra": "allow"}

    @field_validator("page_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError(
                "page_id must be a non-empty slug of [a-zA-Z0-9_-]"
            )
        return v

    def env_prefix(self) -> str:
        """Environment-variable prefix for this page's secrets, e.g. ICE_MOTIVATIONAL_IT."""
        return "ICE_" + self.page_id.upper().replace("-", "_")
