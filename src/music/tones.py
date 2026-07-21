"""Generate calm, original ambient pad tones (CC0 placeholders).

These are synthesized from scratch (sine chords + slow tremolo + soft envelope),
so they carry NO third-party rights — safe to use while a real licensed library
is assembled. They are intentionally simple and quiet: a background pad, not a
song. Replace them with properly licensed tracks for production (see README).
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

SR = 44100

# mood -> (root frequency Hz, chord intervals as semitone offsets, tremolo Hz)
_MOOD_CHORD = {
    "calm": (220.0, (0, 7, 12), 0.10),
    "reflective": (196.0, (0, 3, 10), 0.09),
    "hopeful": (261.63, (0, 4, 7), 0.12),
    "peaceful": (174.61, (0, 7, 12), 0.08),
    "inspirational": (293.66, (0, 4, 11), 0.13),
    "determined": (146.83, (0, 7, 10), 0.14),
    "serene": (207.65, (0, 5, 12), 0.08),
    "resilient": (164.81, (0, 3, 7), 0.12),
    "grateful": (233.08, (0, 4, 9), 0.10),
    "courageous": (155.56, (0, 5, 7), 0.13),
    "focused": (185.0, (0, 7, 14), 0.07),
    "tender": (246.94, (0, 3, 8), 0.09),
}


def _semitone(freq: float, semis: int) -> float:
    return freq * (2.0 ** (semis / 12.0))


def synth_pad(mood: str, duration: float = 20.0, seed: int = 0) -> np.ndarray:
    """Return an (N,2) float32 stereo array in [-1, 1] for a calm pad."""
    root, intervals, trem_hz = _MOOD_CHORD.get(mood, _MOOD_CHORD["calm"])
    n = int(SR * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    rng = np.random.default_rng(seed or 1)

    left = np.zeros(n, dtype=np.float64)
    right = np.zeros(n, dtype=np.float64)
    for i, semis in enumerate(intervals):
        f = _semitone(root, semis)
        detune = 1.0 + (rng.random() - 0.5) * 0.004
        vibrato = 1.0 + 0.0016 * np.sin(2 * math.pi * (0.18 + 0.05 * i) * t)
        phase_l = 2 * math.pi * f * detune * vibrato * t
        phase_r = 2 * math.pi * f / detune * vibrato * t
        amp = 0.9 ** i
        left += amp * np.sin(phase_l)
        right += amp * np.sin(phase_r)
        # a soft second harmonic for warmth
        left += 0.18 * amp * np.sin(2 * phase_l)
        right += 0.18 * amp * np.sin(2 * phase_r)

    # slow tremolo (gentle movement)
    trem = 0.82 + 0.18 * np.sin(2 * math.pi * trem_hz * t)
    left *= trem
    right *= trem

    # normalize then soft attack/release envelope
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-6)
    left /= peak
    right /= peak
    env = np.ones(n)
    a = min(int(SR * 2.0), n // 2)  # 2s attack (clamped for short clips)
    r = min(int(SR * 3.0), n - a)   # 3s release
    if a > 0:
        env[:a] = np.linspace(0, 1, a) ** 1.5
    if r > 0:
        env[-r:] = np.linspace(1, 0, r) ** 1.2
    left *= env * 0.5  # headroom (~-6 dB)
    right *= env * 0.5
    return np.stack([left, right], axis=1).astype(np.float32)


def write_wav(path: str | Path, samples: np.ndarray, sr: int = SR) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def generate_placeholder_track(path: str | Path, mood: str, duration: float = 20.0,
                               seed: int = 0) -> None:
    write_wav(path, synth_pad(mood, duration, seed))
