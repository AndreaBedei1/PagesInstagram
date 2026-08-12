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

#: Date the Reel specifications and the credential requirements below were
#: re-read on developers.facebook.com, one page at a time.
META_SPECS_REVERIFIED_AT = "2026-08-11"

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
# Verified on GRAPH_API_VERIFIED_AT and re-verified on META_SPECS_REVERIFIED_AT.
# The reference states, verbatim: duration "Minimum of 3 seconds; maximum of
# 15 mins", file size "300MB maximum", frame rate "23-60 FPS", audio "AAC,
# 48khz sample rate maximum, 1 or 2 channels", video "HEVC or H264".
#
# The repository documentation used to claim a 90-second ceiling and
# publisher.py enforced it in a literal of its own. The official reference does
# not state 90 seconds anywhere. Nothing outside this module may hold a copy:
# tests/unit/test_go_live_guards.py fails if a second one appears.
# ---------------------------------------------------------------------------
REEL_MIN_SECONDS = 3
REEL_MAX_SECONDS = 15 * 60
REEL_MAX_FILE_BYTES = 300 * 1_000_000

#: Feed image post: JPEG/PNG, 8 MB maximum, 4:5 recommended for a
#: portrait still (1080x1350). The stills this project renders are
#: well under a megabyte.
IMAGE_MAX_FILE_BYTES = 8 * 1_000_000
IMAGE_POST_SIZE = (1080, 1350)
REEL_MAX_HORIZONTAL_PIXELS = 1920
REEL_VIDEO_CODECS = ("h264", "hevc")
REEL_CONTAINERS = ("mp4", "mov")
REEL_MIN_FPS, REEL_MAX_FPS = 23, 60
REEL_MAX_AUDIO_SAMPLE_RATE = 48_000
REEL_RECOMMENDED_ASPECT = "9:16"

#: Stories are a different product with a different ceiling; kept here so the
#: publisher has no numeric limit of its own for either media type.
STORY_MIN_SECONDS = 1
STORY_MAX_SECONDS = 60

#: Published posts per 24-hour moving window, per Instagram account.
#: "Instagram accounts are limited to 100 API-published posts within a 24-hour
#: moving period."
PUBLISHING_LIMIT_PER_24H = 100

#: Long-lived access token lifetime, in days.
LONG_LIVED_TOKEN_DAYS = 60


# ---------------------------------------------------------------------------
# Which credentials each operation actually needs.
#
# The repository disagreed with itself: .env.example said META_APP_ID and
# META_APP_SECRET were "only needed to (re)generate / refresh tokens", while
# preflight.ps1 refused to run without them. Re-read on
# META_SPECS_REVERIFIED_AT, page by page:
#
#   publish        graph.instagram.com/{version}/{ig-user-id}/media[_publish]
#                  Instagram User access token + the two instagram_business_*
#                  permissions. No app id, no app secret.
#   identity       GET graph.instagram.com/{version}/me?fields=user_id,username
#                  The token authenticates itself. No app credentials.
#   expiry/scopes  GET graph.facebook.com/{version}/debug_token
#                  input_token = the token under examination; access_token must
#                  be "an app access token or a developer user access token" —
#                  i.e. app id AND app secret. There is no debug_token on
#                  graph.instagram.com.
#   refresh        GET graph.instagram.com/refresh_access_token
#                  grant_type=ig_refresh_token + the long-lived token. No secret.
#   short -> long  GET graph.instagram.com/access_token
#                  grant_type=ig_exchange_token + client_secret. Secret needed.
#
# So: the app credentials are NOT required to publish, and are required to read
# a token's expiry and scopes. That is the whole of it, and both statements are
# enforced — the first by REQUIRED_TO_PUBLISH, the second by health.py.
#
# THE TWO PAIRS ARE NOT THE SAME PAIR, and this cost a go-live an afternoon.
# The App Dashboard shows an app id and secret in two different places:
#
#   App settings > Basic                      -> the *Meta* app credentials
#   Instagram > API setup with Instagram login -> the *Instagram* app credentials
#
# `ig_exchange_token` wants the Instagram app secret; `debug_token` is a
# Facebook Graph endpoint and wants a Meta app access token. Handing it the
# Instagram app id produces, verified against a real account on 2026-08-11:
#
#   GET /oauth/access_token?grant_type=client_credentials -> 400 code 101
#   GET /debug_token?access_token=<ig-app-id>|<ig-app-secret> -> 400 code 190
#   GET /<ig-app-id> -> 400 code 190
#       "Error validating application. Cannot get application info due to a
#        system error."
#
# The Instagram app id is simply not a node on graph.facebook.com. So
# META_APP_ID / META_APP_SECRET below mean the **Meta** pair; the Instagram
# secret belongs to the token-exchange path and is not interchangeable with it.
# ---------------------------------------------------------------------------

#: Env variables without which no page can publish. Per page, prefixed.
REQUIRED_TO_PUBLISH: tuple[str, ...] = ("IG_USER_ID", "ACCESS_TOKEN")

#: Env variables needed only to read a token's expiry/scopes and to exchange a
#: short-lived token for a long-lived one. Absent, publishing still works.
OPTIONAL_APP_CREDENTIALS: tuple[str, ...] = ("META_APP_ID", "META_APP_SECRET")

#: Permissions Instagram content publishing requires, as listed on the
#: content-publishing page for the Instagram Login flavor.
REQUIRED_PERMISSIONS: tuple[str, ...] = (
    "instagram_business_basic",
    "instagram_business_content_publish",
)

#: Host that serves ``debug_token``. It is *not* the Instagram host.
TOKEN_DEBUG_HOST = "graph.facebook.com"

#: Hosts per API flavor, so nothing else has to know the mapping.
API_HOSTS = {"instagram_login": "graph.instagram.com",
             "facebook_login": "graph.facebook.com"}


# ---------------------------------------------------------------------------
# What each flavor can actually do.
#
# This project shipped configured as `instagram_login` + `resumable`, and
# described itself as uploading local files straight to Meta with no public
# hosting. That combination does not exist. The first real canary got:
#
#   POST graph.instagram.com/v25.0/<ig-user-id>/media
#        upload_type=resumable&media_type=REELS
#   -> Graph API error [100]: The parameter video_url is required
#
# `upload_type=resumable` was simply ignored, and /media asked for what it
# always asks for. The resumable-uploads reference says why, in one line:
# "Only for apps that have implemented Facebook Login for Business." And the
# content-publishing page is equally plain about the alternative: with
# Instagram Login "the media must be hosted on a publicly accessible server at
# the time of the attempt".
#
# So the "no hosting" architecture is real, but it belongs to the Facebook
# Login flavor. Nothing in the codebase said so, and the failure surfaced at
# the worst possible moment — during a live upload, after every gate had gone
# green. Hence a matrix, checked before anything reaches the network.
# ---------------------------------------------------------------------------

#: Flavors that accept ``upload_type=resumable`` and a direct binary upload.
RESUMABLE_FLAVORS: tuple[str, ...] = ("facebook_login",)

#: Permissions per flavor. The names are not interchangeable: the
#: ``instagram_business_*`` pair belongs to Instagram Login and means nothing
#: to a Facebook Page token.
PERMISSIONS_BY_FLAVOR: dict[str, tuple[str, ...]] = {
    "instagram_login": ("instagram_business_basic",
                        "instagram_business_content_publish"),
    "facebook_login": ("instagram_basic", "instagram_content_publish",
                       "pages_read_engagement"),
}

#: What kind of access token each flavor publishes with.
TOKEN_KIND_BY_FLAVOR = {"instagram_login": "Instagram User access token",
                        "facebook_login": "Facebook Page access token"}


def supports_resumable(flavor: str) -> bool:
    return flavor in RESUMABLE_FLAVORS


def check_upload_method(flavor: str, upload_method: str) -> tuple[bool, str]:
    """Is this (flavor, upload_method) pair one Meta actually implements?

    Returns ``(ok, reason)``. Called by ``check-config`` and the preflight so
    an impossible pairing is refused on this machine, in a sentence that says
    what to do, rather than by Meta in the middle of a go-live.
    """
    if flavor not in API_HOSTS:
        return False, (f"api_flavor {flavor!r} sconosciuto: attesi "
                       f"{', '.join(sorted(API_HOSTS))}")
    if upload_method == "resumable" and not supports_resumable(flavor):
        return False, (
            f"upload_method=resumable non è supportato con api_flavor="
            f"{flavor}: il resumable upload esiste solo per le app che "
            f"implementano Facebook Login for Business. Con {flavor} "
            f"POST /media richiede video_url, quindi un media su host "
            f"pubblico. Passa a api_flavor=facebook_login (nessun hosting) "
            f"oppure a upload_method=hosted_url (serve hosting).")
    if upload_method not in ("resumable", "hosted_url"):
        return False, (f"upload_method {upload_method!r} sconosciuto: attesi "
                       f"resumable, hosted_url")
    return True, (f"{flavor} + {upload_method}: supportato "
                  f"({'nessun hosting pubblico' if upload_method == 'resumable' else 'richiede hosting pubblico'})")
