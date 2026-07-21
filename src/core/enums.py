"""Shared enums, the job state machine, and the mood/music vocabulary.

These constants are the *contract* between subsystems (datasets, music,
scheduling, publishing) so they must stay stable. Values are plain strings
(``StrEnum``) so they persist directly to SQLite and JSON.
"""
from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    """Global run mode. Controls whether real publish calls are made."""

    DRY_RUN = "dry_run"
    TEST = "test"
    PRODUCTION = "production"


class MediaType(StrEnum):
    """Internal media artifact kinds produced by the pipeline."""

    FEED_IMAGE = "feed_image"
    FEED_VIDEO = "feed_video"
    STORY_IMAGE = "story_image"
    STORY_VIDEO = "story_video"
    REEL = "reel"


# Mapping from an internal MediaType to the Meta Graph API ``media_type`` value
# and the aspect it is published as.
META_MEDIA_TYPE = {
    MediaType.FEED_IMAGE: "IMAGE",
    MediaType.FEED_VIDEO: "VIDEO",
    MediaType.STORY_IMAGE: "STORIES",
    MediaType.STORY_VIDEO: "STORIES",
    MediaType.REEL: "REELS",
}


class JobStatus(StrEnum):
    """Publication job state machine (see docs/PLAN.md §3)."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    BACKGROUND_GENERATED = "BACKGROUND_GENERATED"
    RENDERED = "RENDERED"
    MEDIA_READY = "MEDIA_READY"
    SCHEDULED = "SCHEDULED"
    UPLOADING = "UPLOADING"
    CONTAINER_CREATED = "CONTAINER_CREATED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    SKIPPED = "SKIPPED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


#: Terminal states — a job in one of these is never processed again.
TERMINAL_JOB_STATES = frozenset(
    {JobStatus.PUBLISHED, JobStatus.SKIPPED, JobStatus.REJECTED}
)

#: States from which a job may be (re)driven toward publication.
ACTIVE_JOB_STATES = frozenset(
    {
        JobStatus.DRAFT,
        JobStatus.VALIDATED,
        JobStatus.BACKGROUND_GENERATED,
        JobStatus.RENDERED,
        JobStatus.MEDIA_READY,
        JobStatus.SCHEDULED,
        JobStatus.UPLOADING,
        JobStatus.CONTAINER_CREATED,
        JobStatus.PUBLISHING,
        JobStatus.RETRY_PENDING,
    }
)


class ContentStatus(StrEnum):
    """Lifecycle of a content row. Only APPROVED items can be published."""

    DRAFT = "draft"
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    APPROVED_FOR_PUBLICATION = "approved_for_publication"


class MissedJobPolicy(StrEnum):
    """What to do with a job whose scheduled time passed while the PC was off."""

    PUBLISH_IMMEDIATELY = "publish_immediately"
    PUBLISH_WITHIN_WINDOW = "publish_within_window"
    SKIP = "skip"
    RESCHEDULE = "reschedule"


# --- Mood & music vocabulary ------------------------------------------------

#: Canonical mood vocabulary shared by datasets and music selection.
MOODS: tuple[str, ...] = (
    "calm",
    "reflective",
    "hopeful",
    "peaceful",
    "inspirational",
    "determined",
    "serene",
    "resilient",
    "grateful",
    "courageous",
    "focused",
    "tender",
)

#: Music library categories (mirror the folders under assets/music/).
MUSIC_CATEGORIES: tuple[str, ...] = (
    "calm",
    "reflective",
    "hopeful",
    "peaceful",
    "inspirational",
    "soft_energy",
    "gentle_piano",
    "ambient",
    "soft_acoustic",
    "light_cinematic",
)

#: Ordered preference of music categories for each mood (first = best fit).
#: Used by the music selector; every mood maps to at least two categories so a
#: sparse library still resolves.
MOOD_TO_MUSIC: dict[str, tuple[str, ...]] = {
    "calm": ("calm", "ambient", "gentle_piano", "peaceful"),
    "reflective": ("reflective", "gentle_piano", "ambient", "calm"),
    "hopeful": ("hopeful", "inspirational", "light_cinematic", "soft_acoustic"),
    "peaceful": ("peaceful", "calm", "ambient", "gentle_piano"),
    "inspirational": ("inspirational", "light_cinematic", "hopeful", "soft_energy"),
    "determined": ("soft_energy", "light_cinematic", "inspirational", "hopeful"),
    "serene": ("peaceful", "calm", "ambient", "reflective"),
    "resilient": ("inspirational", "soft_energy", "hopeful", "light_cinematic"),
    "grateful": ("soft_acoustic", "gentle_piano", "calm", "reflective"),
    "courageous": ("light_cinematic", "inspirational", "soft_energy", "hopeful"),
    "focused": ("ambient", "reflective", "calm", "gentle_piano"),
    "tender": ("gentle_piano", "soft_acoustic", "calm", "peaceful"),
}


def music_categories_for_mood(mood: str) -> tuple[str, ...]:
    """Return the ordered music-category preference for a mood (safe fallback)."""
    return MOOD_TO_MUSIC.get(mood, ("calm", "ambient", "reflective"))
