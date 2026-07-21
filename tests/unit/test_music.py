"""Tests for tone synthesis, library license gating, and mood selection."""
from __future__ import annotations

import json
import wave

import numpy as np

from src.music.library import sync_library
from src.music.selector import select_track
from src.music.tones import synth_pad, write_wav


def test_synth_pad_shape_and_range():
    s = synth_pad("calm", duration=1.0, seed=1)
    assert s.shape == (44100, 2)
    assert float(np.abs(s).max()) <= 1.0


def test_write_wav_is_valid(tmp_path):
    s = synth_pad("hopeful", duration=0.5)
    p = tmp_path / "t.wav"
    write_wav(p, s)
    with wave.open(str(p)) as w:
        assert w.getnchannels() == 2
        assert w.getframerate() == 44100
        assert w.getnframes() == 22050


def test_library_license_gate(tmp_db, tmp_path):
    (tmp_path / "calm").mkdir()
    (tmp_path / "calm" / "ok.wav").write_bytes(b"RIFF0000")
    (tmp_path / "calm" / "nolic.wav").write_bytes(b"RIFF0000")
    catalog = {"tracks": [
        {"track_id": "ok", "title": "OK", "file": "calm/ok.wav", "license": "CC0-1.0",
         "category": "calm", "mood": "calm", "duration_seconds": 20},
        {"track_id": "nolic", "title": "No License", "file": "calm/nolic.wav",
         "license": "", "category": "calm", "mood": "calm", "duration_seconds": 20},
        {"track_id": "missing", "title": "Missing", "file": "calm/absent.wav",
         "license": "CC0-1.0", "category": "calm"},
    ]}
    (tmp_path / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    rep = sync_library(tmp_db, tmp_path)
    assert rep.upserted == 2  # missing file skipped
    assert rep.missing_files == 1
    safe = tmp_db.list_tracks(instagram_safe=True)
    unsafe = tmp_db.list_tracks(instagram_safe=False)
    assert {t["track_id"] for t in safe} == {"ok"}
    assert {t["track_id"] for t in unsafe} == {"nolic"}


def _add(db, tid, category, mood="calm"):
    db.upsert_track(dict(track_id=tid, title=tid, file_path=f"/x/{tid}.wav",
                         license="CC0-1.0", category=category, mood=mood,
                         duration_seconds=20, instagram_safe=1))


def test_selector_matches_mood_category(tmp_db):
    _add(tmp_db, "c1", "calm", "calm")
    _add(tmp_db, "r1", "reflective", "reflective")
    choice = select_track(tmp_db, mood="calm", page_id="p1")
    assert choice is not None
    assert choice.track["category"] == "calm"


def test_selector_avoids_recent(tmp_db):
    _add(tmp_db, "c1", "calm", "calm")
    _add(tmp_db, "c2", "calm", "calm")
    first = select_track(tmp_db, mood="calm", page_id="p1").track["track_id"]
    tmp_db.record_music_usage(first, "p1", None)
    second = select_track(tmp_db, mood="calm", page_id="p1", avoid_last_n=8).track["track_id"]
    assert second != first


def test_selector_none_when_no_safe_tracks(tmp_db):
    tmp_db.upsert_track(dict(track_id="x", title="x", file_path="/x.wav",
                             license="", category="calm", mood="calm",
                             instagram_safe=0))
    assert select_track(tmp_db, mood="calm") is None
