"""FFmpeg resolution, command execution and media probing.

FFmpeg binary resolution order:
  1. ICE_FFMPEG_PATH env / settings.video.ffmpeg_path
  2. ``ffmpeg`` on PATH
  3. the binary bundled with ``imageio-ffmpeg`` (always present as a dependency)

Probing parses ``ffmpeg -i`` stderr (no separate ffprobe needed).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from ..core.errors import VideoError
from ..core.logging_setup import get_logger

log = get_logger("video.ffmpeg")

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_MAXVOL_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_MEANVOL_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_VIDEO_RE = re.compile(r"Stream.*Video:\s*([a-zA-Z0-9]+).*?,\s*(\d+)x(\d+)")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_AUDIO_RE = re.compile(r"Stream.*Audio:\s*([a-zA-Z0-9]+).*?,\s*(\d+)\s*Hz")


def resolve_ffmpeg(ffmpeg_path: str | None = None) -> str:
    if ffmpeg_path and os.path.exists(ffmpeg_path):
        return ffmpeg_path
    env = os.environ.get("ICE_FFMPEG_PATH")
    if env and os.path.exists(env):
        return env
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise VideoError(f"ffmpeg not found: {e}") from e


def run_ffmpeg(ffmpeg: str, args: list[str], *, timeout: int = 300) -> str:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        raise VideoError(f"ffmpeg timed out after {timeout}s") from e
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-12:])
        raise VideoError(f"ffmpeg failed ({proc.returncode}):\n{tail}")
    return proc.stderr or ""


@dataclass
class FFProbeInfo:
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    fps: float | None = None
    has_audio: bool = False
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    size_bytes: int | None = None


def probe_media(path: str, ffmpeg: str | None = None) -> FFProbeInfo:
    ffmpeg = ffmpeg or resolve_ffmpeg()
    info = FFProbeInfo()
    if os.path.exists(path):
        info.size_bytes = os.path.getsize(path)
    # ffmpeg -i returns non-zero (no output specified); we only want stderr.
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    err = proc.stderr or ""
    if (m := _DURATION_RE.search(err)):
        h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info.duration = h * 3600 + mnt * 60 + s
    if (m := _VIDEO_RE.search(err)):
        info.video_codec = m.group(1)
        info.width, info.height = int(m.group(2)), int(m.group(3))
    if (m := _FPS_RE.search(err)):
        info.fps = float(m.group(1))
    if (m := _AUDIO_RE.search(err)):
        info.has_audio = True
        info.audio_codec = m.group(1)
        info.audio_sample_rate = int(m.group(2))
    return info


def measure_loudness(path: str, ffmpeg: str | None = None) -> tuple[float | None, float | None]:
    """Return (max_volume_dB, mean_volume_dB) via the ``volumedetect`` filter."""
    ffmpeg = ffmpeg or resolve_ffmpeg()
    null_target = "NUL" if os.name == "nt" else "/dev/null"
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null",
         null_target],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    err = proc.stderr or ""
    mx = float(m.group(1)) if (m := _MAXVOL_RE.search(err)) else None
    mn = float(m.group(1)) if (m := _MEANVOL_RE.search(err)) else None
    return mx, mn
