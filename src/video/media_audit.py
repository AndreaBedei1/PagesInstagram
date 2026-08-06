"""Verify generated videos against Meta's published Reel specifications.

The pipeline is *configured* to produce H.264 in MP4 at 1080×1920, 30 FPS, with
faststart and a silent AAC track. This asks ffprobe what actually came out —
container, codecs, pixel format, resolution, duration, frame rate, audio sample
rate and channels, and whether the moov atom sits at the front of the file.

Limits come from :mod:`src.core.meta_api`, the same constants the documentation
quotes, so a spec change is a one-line edit in one place.

The geometry check is deliberately not OCR. Instagram overlays its own interface
on a Reel; what matters is that the text block sits inside the safe area the
renderer was told to respect. That is a geometric property of the render, and
the renderer already knows the box it used — this cross-checks the resolution
and aspect ratio that box was computed for.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.meta_api import (REEL_CONTAINERS, REEL_MAX_AUDIO_SAMPLE_RATE,
                             REEL_MAX_FPS, REEL_MAX_SECONDS, REEL_MIN_FPS,
                             REEL_MIN_SECONDS, REEL_VIDEO_CODECS)
from .ffmpeg import resolve_ffmpeg

TARGET_WIDTH, TARGET_HEIGHT = 1080, 1920
TARGET_PIXEL_FORMAT = "yuv420p"
#: Instagram's own interface covers roughly this much of the top and bottom.
STORY_TOP_SAFE = 0.14
STORY_BOTTOM_SAFE = 0.18


@dataclass
class MediaCheck:
    file: str
    page_id: str = ""
    ok: bool = True
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    pixel_format: str = ""
    width: int = 0
    height: int = 0
    duration_seconds: float = 0.0
    fps: float = 0.0
    audio_sample_rate: int = 0
    audio_channels: int = 0
    faststart: bool = False
    size_bytes: int = 0
    aspect_ratio: str = ""
    source: str = ""
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _ffprobe_path(ffmpeg_path: str | None = None) -> str | None:
    """Locate ffprobe, or ``None`` if this installation does not ship one.

    The bundled ``imageio-ffmpeg`` binary is ffmpeg alone — there is no ffprobe
    next to it — so a full-system ffmpeg is checked too and, failing both, the
    audit falls back to parsing ``ffmpeg -i`` (see :func:`probe_file`).
    """
    from shutil import which

    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    candidate = Path(ffmpeg).with_name(
        Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))
    if candidate.exists():
        return str(candidate)
    return which("ffprobe")


def _parse_fraction(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in str(value):
        num, _, den = str(value).partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _gcd_ratio(width: int, height: int) -> str:
    from math import gcd
    if not width or not height:
        return ""
    g = gcd(width, height)
    return f"{width // g}:{height // g}"


_PIXFMT_RE = re.compile(r"Video:\s*\w+[^,]*,\s*([a-z0-9]+)[\s,(]")


def _probe_with_ffmpeg(path: Path, ffmpeg_path: str | None,
                       check: "MediaCheck") -> None:
    """Fill ``check`` by parsing ``ffmpeg -i``, when no ffprobe is installed.

    The bundled imageio-ffmpeg binary ships ffmpeg only. Rather than making the
    audit unavailable on a default install — which is exactly the configuration
    the project documents — it reuses the parser the video pipeline already
    relies on and reads the pixel format from the same stderr block.
    """
    from .ffmpeg import probe_media, resolve_ffmpeg as _resolve

    info = probe_media(str(path), ffmpeg=_resolve(ffmpeg_path))
    check.duration_seconds = round(info.duration or 0.0, 3)
    check.video_codec = info.video_codec or ""
    check.width, check.height = info.width or 0, info.height or 0
    check.fps = round(info.fps or 0.0, 3)
    check.audio_codec = info.audio_codec or ""
    check.audio_sample_rate = info.audio_sample_rate or 0
    check.container = path.suffix.lstrip(".").lower()

    proc = subprocess.run(
        [_resolve(ffmpeg_path), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    err = proc.stderr or ""
    if (m := _PIXFMT_RE.search(err)):
        check.pixel_format = m.group(1)
    if "stereo" in err:
        check.audio_channels = 2
    elif "mono" in err:
        check.audio_channels = 1
    if not info.has_audio:
        check.problems.append(
            "nessuna traccia audio: Meta richiede AAC anche muto")
    if not info.video_codec:
        check.problems.append("nessuna traccia video")


def probe_file(path: str | Path, *, page_id: str = "",
               ffmpeg_path: str | None = None) -> MediaCheck:
    """Everything ffprobe knows about one file, plus the verdict."""
    p = Path(path)
    check = MediaCheck(file=p.name, page_id=page_id)
    if not p.exists():
        check.ok = False
        check.problems.append("file inesistente")
        return check
    check.size_bytes = p.stat().st_size

    probe = _ffprobe_path(ffmpeg_path)
    if probe:
        check.source = "ffprobe"
        cmd = [probe, "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(p)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                 check=False)
            data = json.loads(out.stdout or "{}")
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            check.ok = False
            check.problems.append(f"ffprobe non eseguibile: {e}")
            return check

        fmt = data.get("format") or {}
        check.container = str(fmt.get("format_name") or "")
        check.duration_seconds = round(_parse_fraction(fmt.get("duration")), 3)

        video = next((s for s in data.get("streams") or []
                      if s.get("codec_type") == "video"), None)
        audio = next((s for s in data.get("streams") or []
                      if s.get("codec_type") == "audio"), None)

        if video is None:
            check.problems.append("nessuna traccia video")
        else:
            check.video_codec = str(video.get("codec_name") or "")
            check.pixel_format = str(video.get("pix_fmt") or "")
            check.width = int(video.get("width") or 0)
            check.height = int(video.get("height") or 0)
            check.fps = round(_parse_fraction(video.get("avg_frame_rate")), 3)

        if audio is None:
            check.problems.append(
                "nessuna traccia audio: Meta richiede AAC anche muto")
        else:
            check.audio_codec = str(audio.get("codec_name") or "")
            check.audio_sample_rate = int(audio.get("sample_rate") or 0)
            check.audio_channels = int(audio.get("channels") or 0)
    else:
        check.source = "ffmpeg -i"
        _probe_with_ffmpeg(p, ffmpeg_path, check)

    check.aspect_ratio = _gcd_ratio(check.width, check.height)

    # moov atom at the front — ffprobe does not report it, so read the head.
    try:
        head = p.open("rb").read(64 * 1024)
        moov, mdat = head.find(b"moov"), head.find(b"mdat")
        check.faststart = moov != -1 and (mdat == -1 or moov < mdat)
    except OSError:
        check.faststart = False

    # -- verdicts ---------------------------------------------------------
    if not any(c in check.container for c in REEL_CONTAINERS):
        check.problems.append(
            f"contenitore {check.container!r}: attesi {REEL_CONTAINERS}")
    if check.video_codec not in REEL_VIDEO_CODECS:
        check.problems.append(
            f"codec video {check.video_codec!r}: attesi {REEL_VIDEO_CODECS}")
    if check.pixel_format != TARGET_PIXEL_FORMAT:
        check.problems.append(
            f"pixel format {check.pixel_format!r}: atteso {TARGET_PIXEL_FORMAT}")
    if (check.width, check.height) != (TARGET_WIDTH, TARGET_HEIGHT):
        check.problems.append(
            f"risoluzione {check.width}×{check.height}: attesa "
            f"{TARGET_WIDTH}×{TARGET_HEIGHT}")
    if check.aspect_ratio != "9:16":
        check.problems.append(f"proporzioni {check.aspect_ratio}: attese 9:16")
    if not REEL_MIN_SECONDS <= check.duration_seconds <= REEL_MAX_SECONDS:
        check.problems.append(
            f"durata {check.duration_seconds}s fuori dai limiti Meta "
            f"({REEL_MIN_SECONDS}–{REEL_MAX_SECONDS}s)")
    if not REEL_MIN_FPS <= check.fps <= REEL_MAX_FPS:
        check.problems.append(
            f"frame rate {check.fps}: atteso fra {REEL_MIN_FPS} e {REEL_MAX_FPS}")
    if check.audio_codec and check.audio_codec != "aac":
        check.problems.append(f"codec audio {check.audio_codec!r}: atteso aac")
    if check.audio_sample_rate > REEL_MAX_AUDIO_SAMPLE_RATE:
        check.problems.append(
            f"sample rate {check.audio_sample_rate} Hz: massimo "
            f"{REEL_MAX_AUDIO_SAMPLE_RATE}")
    if check.audio_channels not in (0, 1, 2):
        check.problems.append(f"{check.audio_channels} canali audio: attesi 1 o 2")
    if not check.faststart:
        check.problems.append(
            "moov atom non in testa al file: manca +faststart")

    check.ok = not check.problems
    return check


def audit_files(files: list[tuple[str, str]], *,
                ffmpeg_path: str | None = None) -> list[MediaCheck]:
    """``[(page_id, path), …]`` -> one :class:`MediaCheck` each."""
    return [probe_file(path, page_id=page_id, ffmpeg_path=ffmpeg_path)
            for page_id, path in files]


def safe_area_box(width: int = TARGET_WIDTH, height: int = TARGET_HEIGHT,
                  *, margin_ratio: float = 0.08) -> dict:
    """The rectangle text must stay inside on a 9:16 Reel shared to the feed.

    Instagram draws its own controls over the top and bottom of a Reel, and the
    feed preview crops differently again. This is the box the renderer targets;
    reporting it next to the measured resolution is what makes the geometric
    check meaningful instead of a claim.
    """
    side = int(min(width, height) * margin_ratio)
    top = int(height * STORY_TOP_SAFE)
    bottom = int(height * STORY_BOTTOM_SAFE)
    return {"left": side, "right": width - side, "top": top,
            "bottom": height - bottom,
            "usable_width": width - 2 * side,
            "usable_height": height - top - bottom}


def write_report(checks: list[MediaCheck], out_path: str | Path) -> Path:
    from datetime import datetime, timezone

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": all(c.ok for c in checks),
        "targets": {
            "resolution": f"{TARGET_WIDTH}x{TARGET_HEIGHT}",
            "pixel_format": TARGET_PIXEL_FORMAT,
            "video_codecs": list(REEL_VIDEO_CODECS),
            "containers": list(REEL_CONTAINERS),
            "fps_range": [REEL_MIN_FPS, REEL_MAX_FPS],
            "duration_range_seconds": [REEL_MIN_SECONDS, REEL_MAX_SECONDS],
            "max_audio_sample_rate": REEL_MAX_AUDIO_SAMPLE_RATE,
        },
        "safe_area": safe_area_box(),
        "files": [c.as_dict() for c in checks],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
