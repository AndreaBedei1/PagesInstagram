"""Single source of truth for the Meta Graph API version and its published specs.

Four places used to carry their own copy of the version string — the Python
settings, ``config/settings.yaml``, ``.env.example`` and the operational
documentation — and they had already drifted apart once. Everything now reads
:data:`DEFAULT_GRAPH_API_VERSION`, and ``tests/unit/test_meta_api_version.py``
fails if any of the four disagrees.

It lives in ``core`` rather than ``publishing`` so that ``core.settings`` can
use it as a field default without importing the publishing package (which
imports ``core`` in turn).

The version stays configurable at runtime through ``META_GRAPH_API_VERSION``.

Verification date and sources are recorded in ``docs/META_RESUMABLE_UPLOAD.md``.
"""
from __future__ import annotations

import re

#: Default Graph API version used by the client.
#:
#: Chosen as the version Meta's **own Instagram Content Publishing examples**
#: use, rather than the newest release: at the verification date v26.0 had been
#: available for one week and no Instagram content-publishing page used it in an
#: example yet. v25.0 is generally available until 2028-07-29, which leaves ample
#: room before a forced bump.
DEFAULT_GRAPH_API_VERSION = "v25.0"

#: Date the version and the resumable-upload flow were last checked against the
#: official Meta documentation (ISO date, used by the docs and the tests).
GRAPH_API_VERIFIED_AT = "2026-08-05"

#: Newest version listed in the official Graph API changelog on that date.
LATEST_GRAPH_API_VERSION = "v26.0"

#: Official pages consulted. Only developers.facebook.com — no blogs, no
#: third-party repositories, no Stack Overflow.
OFFICIAL_SOURCES: tuple[str, ...] = (
    "https://developers.facebook.com/docs/graph-api/changelog",
    "https://developers.facebook.com/docs/instagram-platform/content-publishing",
    "https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/",
    "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media/",
    "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started",
)

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)$")


def valid_version(value: str | None) -> bool:
    """``vNN.N`` as Meta writes it — nothing else is accepted."""
    return bool(value and _VERSION_RE.match(str(value)))


def version_tuple(value: str) -> tuple[int, int]:
    m = _VERSION_RE.match(str(value))
    if not m:
        raise ValueError(f"versione Graph API non valida: {value!r}")
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------------------
# Reel specifications, as documented on the IG User /media reference.
# Verified on GRAPH_API_VERIFIED_AT. The previous documentation claimed a
# 90-second maximum, which the official reference does not state: the documented
# limit is 15 minutes.
# ---------------------------------------------------------------------------
REEL_MIN_SECONDS = 3
REEL_MAX_SECONDS = 15 * 60
REEL_VIDEO_CODECS = ("h264", "hevc")
REEL_CONTAINERS = ("mp4", "mov")
REEL_MIN_FPS, REEL_MAX_FPS = 23, 60
REEL_MAX_AUDIO_SAMPLE_RATE = 48_000
REEL_RECOMMENDED_ASPECT = "9:16"

#: Published posts per 24-hour moving window, per Instagram account.
PUBLISHING_LIMIT_PER_24H = 100

#: Long-lived access token lifetime, in days.
LONG_LIVED_TOKEN_DAYS = 60
