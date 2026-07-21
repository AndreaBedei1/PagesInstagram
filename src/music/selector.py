"""Mood-based music selection with recency avoidance and usage balancing."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..core.enums import music_categories_for_mood
from ..database import Database


@dataclass
class TrackChoice:
    track: dict
    reason: str


def _stable_jitter(track_id: str) -> float:
    h = hashlib.sha256(track_id.encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF  # deterministic 0..1 tiebreak


def select_track(
    db: Database,
    *,
    mood: str | None,
    page_id: str | None = None,
    duration_needed: float | None = None,
    avoid_last_n: int = 8,
) -> TrackChoice | None:
    """Pick the best license-safe track for a mood.

    Scoring (lower is better): category preference index, then a penalty for
    recently-used tracks, then global usage count, then a deterministic jitter.
    Only ``instagram_safe`` tracks are considered.
    """
    categories = music_categories_for_mood(mood or "calm")
    recent = set(db.recent_track_ids(page_id, avoid_last_n))
    usage = db.track_usage_counts()

    best: tuple[tuple, dict] | None = None
    for cat_rank, category in enumerate(categories):
        for track in db.list_tracks(category=category, instagram_safe=True):
            tid = track["track_id"]
            dur = track.get("duration_seconds") or 0
            # tracks shorter than needed are allowed (looped by the video layer)
            duration_penalty = 0.0
            if duration_needed and dur and dur < duration_needed * 0.5:
                duration_penalty = 0.5
            mood_bonus = -0.4 if track.get("mood") == mood else 0.0
            key = (
                cat_rank,
                1 if tid in recent else 0,
                usage.get(tid, 0),
                round(duration_penalty + mood_bonus + _stable_jitter(tid) * 0.1, 4),
            )
            if best is None or key < best[0]:
                best = (key, track)

    if best is None:
        return None
    _, track = best
    reason = f"mood={mood} cat={track.get('category')} usage={usage.get(track['track_id'],0)}"
    return TrackChoice(track=track, reason=reason)
