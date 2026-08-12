"""Verify a rendered still against what Meta accepts for an image post.

The video counterpart of this module (:mod:`src.video.media_audit`) shells out
to ffprobe, because a Reel can be wrong in a dozen invisible ways — codec,
pixel format, frame rate, a moov atom in the wrong place, an audio track that
should not exist. A still has none of those. What can be wrong with it is
short enough to list: it can be absent, empty, not an image at all, the wrong
shape, or too large. Pillow answers all five without a subprocess, which also
means the canary no longer needs ffmpeg on a machine that has no ffmpeg.

The limits come from :mod:`src.core.meta_api`, the same constants the publisher
enforces, so there is one place to change if Meta changes them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.meta_api import IMAGE_MAX_FILE_BYTES, IMAGE_POST_SIZE

#: Extensions Meta documents for an image post.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

#: Pillow's names for the formats behind those extensions.
IMAGE_FORMATS = ("JPEG", "PNG")


@dataclass
class ImageCheck:
    """Deliberately the same shape as :class:`~src.video.media_audit.MediaCheck`.

    The canary treats both kinds of media through one code path, so the two
    verdicts have to be readable the same way: ``ok`` plus a list of sentences
    a human can act on.
    """

    file: str = ""
    page_id: str = ""
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    image_format: str = ""
    source: str = "pillow"

    def fail(self, problem: str) -> "ImageCheck":
        self.ok = False
        self.problems.append(problem)
        return self

    def as_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def audit_image(path: str | Path, *, page_id: str = "") -> ImageCheck:
    """Everything that can be wrong with a still, in one pass."""
    p = Path(path)
    check = ImageCheck(file=p.name, page_id=page_id)
    if not p.exists():
        return check.fail("file inesistente")

    check.size_bytes = p.stat().st_size
    if check.size_bytes <= 0:
        return check.fail("file vuoto")
    if p.suffix.lower() not in IMAGE_EXTENSIONS:
        return check.fail(f"estensione {p.suffix or '(nessuna)'}: "
                          f"un post immagine richiede JPEG o PNG")
    if check.size_bytes > IMAGE_MAX_FILE_BYTES:
        check.fail(f"{check.size_bytes / 1_000_000:.1f} MB: il massimo "
                   f"documentato è {IMAGE_MAX_FILE_BYTES // 1_000_000} MB")

    try:
        from PIL import Image

        with Image.open(p) as im:
            check.width, check.height = im.size
            check.image_format = im.format or ""
    except Exception as e:  # noqa: BLE001 — any unreadable file is a failure
        return check.fail(f"immagine non leggibile: {e}")

    if check.image_format not in IMAGE_FORMATS:
        check.fail(f"formato {check.image_format or 'sconosciuto'}: "
                   f"attesi {', '.join(IMAGE_FORMATS)}")
    if (check.width, check.height) != IMAGE_POST_SIZE:
        check.fail(f"{check.width}x{check.height}: il post immagine è "
                   f"{IMAGE_POST_SIZE[0]}x{IMAGE_POST_SIZE[1]} (4:5)")
    return check
