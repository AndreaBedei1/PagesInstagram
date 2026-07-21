"""Assemble Instagram-compliant videos from a still image + music.

Output profile (matches Meta's documented requirements):
  - MP4 / H.264 High, progressive, yuv420p, closed-ish GOP, configurable fps
  - AAC 48 kHz stereo, faded in/out and volume-attenuated under the (text) content
  - moov atom at the front (``+faststart``)
Optional gentle Ken Burns zoom keeps a static composition alive.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.logging_setup import get_logger
from ..core.settings import Settings
from .ffmpeg import probe_media, resolve_ffmpeg, run_ffmpeg

log = get_logger("video.builder")


@dataclass
class VideoResult:
    path: Path
    duration: float
    width: int
    height: int
    fps: int
    has_audio: bool
    size_bytes: int
    source_image: str
    music_path: str | None
    metadata: dict = field(default_factory=dict)


class VideoBuilder:
    def __init__(self, settings: Settings):
        self.s = settings
        self.ffmpeg = resolve_ffmpeg(settings.video.ffmpeg_path)

    def _dims(self, aspect: str) -> tuple[int, int]:
        return tuple(self.s.rendering.story_size if aspect == "story"
                     else self.s.rendering.post_size)

    def build(
        self,
        *,
        image_path: str | Path,
        out_path: str | Path,
        aspect: str,
        duration: float | None = None,
        music_path: str | Path | None = None,
        volume_db: float | None = None,
        fade_in: float | None = None,
        fade_out: float | None = None,
        ken_burns: bool | None = None,
    ) -> VideoResult:
        s = self.s
        W, H = self._dims(aspect)
        dur = float(duration if duration is not None
                    else (s.video.story_duration_seconds if aspect == "story"
                          else s.video.feed_duration_seconds))
        fps = s.video.fps
        frames = max(2, int(round(dur * fps)))
        kb = s.video.ken_burns if ken_burns is None else ken_burns
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if kb:
            zoom = s.video.ken_burns_zoom
            bw, bh = int(W * 1.5), int(H * 1.5)
            vf = (
                f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
                f"zoompan=z='min(1.0+{zoom - 1:.4f}*on/{frames - 1},{zoom})'"
                f":d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={fps},"
                f"format=yuv420p"
            )
        else:
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},format=yuv420p")

        has_audio = bool(music_path and os.path.exists(str(music_path)))
        args: list[str] = ["-loop", "1", "-i", str(image_path)]
        if has_audio:
            vol = s.music.default_volume_db if volume_db is None else volume_db
            fi = s.music.fade_in_seconds if fade_in is None else fade_in
            fo = s.music.fade_out_seconds if fade_out is None else fade_out
            args += ["-stream_loop", "-1", "-i", str(music_path)]
            af = (f"afade=t=in:st=0:d={fi:.3f},"
                  f"afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f},"
                  f"volume={vol}dB")
            args += ["-filter_complex", f"[0:v]{vf}[v];[1:a]{af}[a]",
                     "-map", "[v]", "-map", "[a]"]
        else:
            args += ["-vf", vf, "-map", "0:v"]

        args += [
            "-t", f"{dur:.3f}", "-r", str(fps),
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-crf", str(s.video.video_crf),
            "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0",
        ]
        if has_audio:
            args += ["-c:a", "aac", "-b:a", s.video.audio_bitrate, "-ar", "48000", "-ac", "2"]
        args += ["-movflags", "+faststart", str(out_path)]

        run_ffmpeg(self.ffmpeg, args, timeout=max(120, int(dur * 20)))
        info = probe_media(str(out_path), self.ffmpeg)
        log.info("video built %s (%.1fs, audio=%s) -> %s", aspect, dur, has_audio,
                 out_path.name)
        return VideoResult(
            path=out_path,
            duration=info.duration or dur,
            width=info.width or W,
            height=info.height or H,
            fps=fps,
            has_audio=has_audio and info.has_audio,
            size_bytes=info.size_bytes or (out_path.stat().st_size if out_path.exists() else 0),
            source_image=str(image_path),
            music_path=str(music_path) if has_audio else None,
            metadata={"aspect": aspect, "ken_burns": kb,
                      "video_codec": info.video_codec, "audio_codec": info.audio_codec,
                      "audio_sr": info.audio_sample_rate},
        )
