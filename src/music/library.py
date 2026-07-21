"""Music library: a tracked JSON catalog synced into the DB, plus a generator
for CC0 placeholder tones so the pipeline always has license-clean audio.

Safety rule: a track with no explicit license is marked ``instagram_safe=0`` and
excluded from automatic selection. Only tracks with a recorded license/provenance
are used for publishing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.enums import MOOD_TO_MUSIC, MOODS
from ..core.logging_setup import get_logger
from ..database import Database
from .tones import generate_placeholder_track

log = get_logger("music.library")

CATALOG_NAME = "catalog.json"


@dataclass
class LibraryReport:
    scanned: int = 0
    upserted: int = 0
    unlicensed: int = 0
    missing_files: int = 0
    generated: int = 0
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"tracks scanned={self.scanned} upserted={self.upserted} "
                f"unlicensed={self.unlicensed} missing={self.missing_files} "
                f"generated={self.generated}")


def _catalog_path(music_root: Path) -> Path:
    return music_root / CATALOG_NAME


def load_catalog(music_root: Path) -> list[dict]:
    p = _catalog_path(music_root)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("tracks", data) if isinstance(data, dict) else data


def save_catalog(music_root: Path, tracks: list[dict]) -> None:
    _catalog_path(music_root).write_text(
        json.dumps({"tracks": tracks}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def sync_library(db: Database, music_root: Path) -> LibraryReport:
    """Read ``catalog.json`` and upsert tracks into the DB (license-gated)."""
    report = LibraryReport()
    tracks = load_catalog(music_root)
    report.scanned = len(tracks)
    for entry in tracks:
        rel = entry.get("file")
        if not rel:
            report.messages.append(f"{entry.get('track_id')}: manca 'file'")
            continue
        path = (music_root / rel).resolve()
        if not path.exists():
            report.missing_files += 1
            report.messages.append(f"file mancante: {rel}")
            continue
        licensed = bool(entry.get("license"))
        safe = licensed and bool(entry.get("instagram_safe", True))
        if not licensed:
            report.unlicensed += 1
        db.upsert_track({
            "track_id": entry.get("track_id") or Path(rel).stem,
            "title": entry.get("title") or Path(rel).stem,
            "author": entry.get("author"),
            "file_path": str(path),
            "license": entry.get("license"),
            "source": entry.get("source"),
            "category": entry.get("category"),
            "mood": entry.get("mood"),
            "duration_seconds": entry.get("duration_seconds"),
            "bpm": entry.get("bpm"),
            "intensity": entry.get("intensity"),
            "instagram_safe": 1 if safe else 0,
            "attribution_required": 1 if entry.get("attribution_required") else 0,
            "attribution_text": entry.get("attribution_text"),
        })
        report.upserted += 1
    return report


def generate_placeholder_library(music_root: Path, *, duration: float = 20.0,
                                 per_mood: int = 1) -> LibraryReport:
    """Synthesize one/two CC0 pad(s) per mood and (re)write the catalog."""
    report = LibraryReport()
    tracks = load_catalog(music_root)
    by_id = {t.get("track_id"): t for t in tracks}
    for mi, mood in enumerate(MOODS):
        category = MOOD_TO_MUSIC[mood][0]
        for k in range(per_mood):
            track_id = f"tone_{mood}_{k+1}"
            rel = f"{category}/{track_id}.wav"
            out = music_root / rel
            seed = mi * 100 + k + 1
            generate_placeholder_track(out, mood, duration=duration, seed=seed)
            report.generated += 1
            by_id[track_id] = {
                "track_id": track_id,
                "title": f"{mood.capitalize()} Pad {k+1}",
                "author": "Instagram Content Engine (generated)",
                "file": rel,
                "license": "CC0-1.0",
                "source": "synthesized (original)",
                "category": category,
                "mood": mood,
                "duration_seconds": duration,
                "bpm": None,
                "intensity": round(0.15 + 0.03 * (mi % 4), 2),
                "instagram_safe": True,
                "attribution_required": False,
                "attribution_text": None,
            }
    merged = list(by_id.values())
    save_catalog(music_root, merged)
    report.scanned = len(merged)
    return report
