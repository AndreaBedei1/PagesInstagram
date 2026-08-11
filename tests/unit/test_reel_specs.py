"""The Reel limits, and the rule that there is only one copy of them.

``core.meta_api`` said 15 minutes, ``publisher.py`` enforced 90 seconds in a
literal of its own, and the operational documentation repeated the 90. Two of
the three were wrong. The official IG User /media reference states, verbatim,
"Minimum of 3 seconds; maximum of 15 mins" and "300MB maximum".

The boundaries are tested with a mocked probe: proving that 901 seconds is
rejected does not require producing fifteen minutes of video.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.core.enums import MediaType, Mode
from src.core.meta_api import REEL_MAX_SECONDS, REEL_MIN_SECONDS
from src.core.settings import load_settings
from src.publishing import MockGraphClient, Publisher
from src.publishing.publisher import PublishTarget

ROOT = Path(__file__).resolve().parents[2]

# =========================================================================
# 1. Reel duration: one limit, read from core.meta_api
# =========================================================================
@pytest.mark.parametrize("seconds,accepted", [
    (2.9, False),     # below the documented minimum
    (3.0, True),      # the documented minimum, inclusive
    (90.0, True),     # the old hard-coded ceiling — a valid Reel, not a failure
    (91.0, True),
    (899.0, True),
    (900.0, True),    # 15 minutes, the documented maximum, inclusive
    (901.0, False),
])
def test_publisher_reel_duration_matches_the_documented_limits(
        tmp_db, project_paths, monkeypatch, seconds, accepted):
    """The probe is mocked: no giant files are produced to test a boundary."""
    from src.core.errors import PublishError
    from src.video import ffmpeg as ffmpeg_mod

    class _Probe:
        duration = seconds

    monkeypatch.setattr(ffmpeg_mod, "probe_media", lambda *_a, **_k: _Probe())

    settings = load_settings(project_paths, load_dotenv=False)
    settings.mode = Mode.TEST
    from src.accounts import load_pages
    publisher = Publisher(settings, tmp_db, load_pages(project_paths),
                          client_factory=lambda p: PublishTarget(MockGraphClient(), "ig"))

    path = Path(project_paths.stories) / f"dur-{seconds}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 1024)

    if accepted:
        assert publisher._validate_video_file(str(path), MediaType.REEL) == 1024
    else:
        with pytest.raises(PublishError) as excinfo:
            publisher._validate_video_file(str(path), MediaType.REEL)
        assert excinfo.value.code == "DURATION"


def test_no_module_keeps_its_own_copy_of_the_reel_limits():
    """A second copy of a limit is a second chance to get it wrong."""
    offenders = []
    pattern = re.compile(r"\b3\s*<=\s*\w+\s*<=\s*90\b|\bdur\w*\s*[<>]=?\s*90\b")
    for path in sorted((ROOT / "src").rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        if pattern.search(body):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"limite Reel duplicato in: {offenders}"


def test_meta_api_reel_limits_are_the_documented_ones():
    assert (REEL_MIN_SECONDS, REEL_MAX_SECONDS) == (3, 900)


