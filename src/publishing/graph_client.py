"""Thin client for the official Meta Instagram Content Publishing API.

Implements the documented 3-step flow:
  1. POST /{ig-user-id}/media           -> container id
  2. GET  /{container-id}?fields=status_code
  3. POST /{ig-user-id}/media_publish   -> media id
Plus GET /{ig-user-id}/content_publishing_limit.

Base host depends on the API flavor:
  - instagram_login -> graph.instagram.com   (permissions: instagram_business_*)
  - facebook_login  -> graph.facebook.com    (permissions: instagram_content_publish)

Errors are wrapped in PublishError with a ``retryable`` flag so the publisher can
apply exponential backoff only to transient failures.
"""
from __future__ import annotations

import os

import requests

from ..core.errors import PublishError
from ..core.logging_setup import get_logger, register_secret
from ..core.meta_api import (API_HOSTS, DEFAULT_GRAPH_API_VERSION,
                             TOKEN_DEBUG_HOST)

log = get_logger("publishing.graph")

# Meta error codes that are transient/worth retrying.
_RETRYABLE_CODES = {1, 2, 4, 17, 32, 341, 613}  # rate limit / temporary
# Codes that mean the token/permission is broken (do NOT retry blindly).
_AUTH_CODES = {190, 102, 10, 200, 803}


class GraphClient:
    def __init__(self, access_token: str, *,
                 api_version: str = DEFAULT_GRAPH_API_VERSION,
                 flavor: str = "instagram_login", session: requests.Session | None = None,
                 timeout: int = 60, upload_timeout: int = 600):
        self.token = access_token
        register_secret(access_token)  # never let the token reach a log
        self.api_version = api_version
        self.flavor = flavor
        host = API_HOSTS.get(flavor, API_HOSTS["facebook_login"])
        self.base = f"https://{host}/{api_version}"
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self._s = session or requests.Session()

    # -- low-level ---------------------------------------------------------
    def _request(self, method: str, path: str, **params) -> dict:
        url = f"{self.base}/{path.lstrip('/')}"
        params["access_token"] = self.token
        try:
            r = self._s.request(method, url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise PublishError(f"network error: {e}", retryable=True) from e
        return self._parse(r)

    def _parse(self, r: requests.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            code = err.get("code")
            msg = err.get("message", f"HTTP {r.status_code}")
            retryable = (r.status_code >= 500) or (code in _RETRYABLE_CODES)
            raise PublishError(f"Graph API error [{code}]: {msg}",
                               retryable=retryable, code=str(code))
        return data

    # -- API surface -------------------------------------------------------
    def create_media_container(self, ig_user_id: str, *, media_type: str,
                               image_url: str | None = None,
                               video_url: str | None = None,
                               caption: str | None = None, **extra) -> str:
        """Create a media container. media_type: IMAGE|VIDEO|REELS|STORIES|CAROUSEL."""
        params: dict = {}
        if media_type != "IMAGE":
            params["media_type"] = media_type
        if image_url:
            params["image_url"] = image_url
        if video_url:
            params["video_url"] = video_url
        if caption:
            params["caption"] = caption
        params.update(extra)
        data = self._request("POST", f"{ig_user_id}/media", **params)
        cid = data.get("id")
        if not cid:
            raise PublishError(f"no container id in response: {data}")
        return cid

    def get_container_status(self, container_id: str) -> str:
        data = self._request("GET", container_id, fields="status_code")
        return data.get("status_code", "UNKNOWN")

    def publish_container(self, ig_user_id: str, creation_id: str) -> str:
        data = self._request("POST", f"{ig_user_id}/media_publish",
                             creation_id=creation_id)
        mid = data.get("id")
        if not mid:
            raise PublishError(f"no media id in publish response: {data}")
        return mid

    # -- resumable (direct) upload ----------------------------------------
    def create_resumable_container(self, ig_user_id: str, *, media_type: str,
                                   caption: str | None = None,
                                   share_to_feed: bool | None = None,
                                   **extra) -> tuple[str, str]:
        """Create a resumable container. Returns (container_id, upload_uri).

        ``media_type`` = REELS | STORIES. ``share_to_feed`` applies to REELS only.
        The returned ``uri`` targets rupload.facebook.com and is what we POST bytes to.
        """
        params: dict = {"upload_type": "resumable", "media_type": media_type}
        if caption is not None:
            params["caption"] = caption
        if share_to_feed is not None:
            params["share_to_feed"] = "true" if share_to_feed else "false"
        params.update(extra)
        data = self._request("POST", f"{ig_user_id}/media", **params)
        cid, uri = data.get("id"), data.get("uri")
        if not cid or not uri:
            raise PublishError(f"resumable container response missing id/uri: {data}")
        return cid, uri

    def upload_video_file(self, upload_uri: str, file_path: str, *, offset: int = 0,
                          timeout: int | None = None) -> dict:
        """Stream a local video to ``upload_uri`` starting at ``offset`` (bytes).

        The file object is passed directly to requests, which streams it in chunks
        and computes Content-Length as (file_size - offset) — the whole file is
        never loaded into memory. Returns the parsed upload response.
        """
        size = os.path.getsize(file_path)
        headers = {
            "Authorization": f"OAuth {self.token}",  # 'OAuth' prefix, not 'Bearer'
            "offset": str(int(offset)),
            "file_size": str(size),
        }
        try:
            with open(file_path, "rb") as fh:
                if offset:
                    fh.seek(offset)
                r = self._s.post(upload_uri, headers=headers, data=fh,
                                 timeout=timeout or self.upload_timeout)
        except requests.RequestException as e:
            # connection interrupted mid-upload -> retryable (resume from offset)
            raise PublishError(f"upload network error: {e}", retryable=True) from e
        return self._parse_upload(r)

    def get_upload_status(self, container_id: str) -> dict:
        """Return {status_code, bytes_transferred|None} for a resumable container."""
        data = self._request("GET", container_id, fields="status_code,video_status")
        vs = data.get("video_status") or {}
        up = vs.get("uploading_phase") or {}
        bt = up.get("bytes_transferred")
        return {"status_code": data.get("status_code", "UNKNOWN"),
                "bytes_transferred": int(bt) if bt is not None else None}

    def get_account_info(self, ig_user_id: str) -> dict:
        """Best-effort account info (id, username, account_type)."""
        try:
            return self._request("GET", ig_user_id, fields="user_id,username,account_type")
        except PublishError:
            # some deployments expose these on /me
            return self._request("GET", "me", fields="user_id,username,account_type")

    def _parse_upload(self, r: requests.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if data.get("success"):
            return data
        if r.status_code >= 400 or "error" in data or "debug_info" in data:
            dbg = data.get("debug_info") or {}
            err = data.get("error") or {}
            retryable = (bool(dbg.get("retriable")) or r.status_code >= 500
                         or err.get("code") in _RETRYABLE_CODES)
            msg = dbg.get("message") or err.get("message") or f"HTTP {r.status_code}"
            raise PublishError(f"upload error: {msg}", retryable=retryable,
                               code=str(dbg.get("type") or err.get("code") or r.status_code))
        # No explicit success and no explicit error → ambiguous → treat as retryable
        raise PublishError(f"ambiguous upload response (HTTP {r.status_code})",
                           retryable=True, code="AMBIGUOUS")

    def get_publishing_limit(self, ig_user_id: str) -> dict:
        return self._request("GET", f"{ig_user_id}/content_publishing_limit",
                             fields="config,quota_usage")

    # -- token inspection --------------------------------------------------
    def verify_token(self, ig_user_id: str | None = None) -> dict:
        """Ask the API who this token belongs to. Raises on an invalid token.

        Flavor-dependent, because the two are not variations of one thing:

        * ``instagram_login`` — the token *is* the account, so ``/me`` answers
          with ``user_id``, ``username`` and ``account_type``.
        * ``facebook_login`` — the token belongs to a **Page**, and ``/me``
          answers about the Page, not about Instagram. The Instagram
          professional account is a separate node, reached by its own id.

        Passing an Instagram User token to graph.facebook.com does not fail
        politely, it fails with "Cannot parse access token" (190): the two
        flavors do not share a token namespace at all.
        """
        if self.flavor == "facebook_login":
            if not ig_user_id:
                raise PublishError(
                    "facebook_login: serve l'Instagram Business Account ID per "
                    "verificare il token (il Page token non descrive da solo "
                    "l'account Instagram)", retryable=False, code="NO_IG_ID")
            data = self._request("GET", ig_user_id,
                                 fields="username,name,profile_picture_url")
            # Shaped like the Instagram Login answer so callers stay
            # flavor-blind — but account_type is left empty rather than
            # asserted. This flavor does not report it, and writing "BUSINESS"
            # here would be the code telling itself something it never read.
            return {"user_id": data.get("id", ig_user_id),
                    "username": data.get("username"),
                    "account_type": ""}
        return self._request("GET", "me",
                             fields="user_id,username,account_type")

    # -- Facebook Login discovery ------------------------------------------
    def list_pages(self) -> list[dict]:
        """Pages this **User** token administers, with their Instagram account.

        Only meaningful for the facebook_login flavor and only with a *User*
        access token — the Page tokens it returns are what publishing actually
        uses. Every one of them is registered with the log redactor before this
        returns, because a list of Page tokens is a list of credentials.
        """
        data = self._request(
            "GET", "me/accounts",
            fields="id,name,access_token,instagram_business_account{id,username}")
        pages = data.get("data") or []
        for page in pages:
            if page.get("access_token"):
                register_secret(page["access_token"])
        return pages

    def instagram_account_for_page(self, page_id: str) -> dict:
        """``GET /{page-id}?fields=instagram_business_account``."""
        data = self._request("GET", page_id,
                             fields="name,instagram_business_account{id,username}")
        return data.get("instagram_business_account") or {}

    def debug_token(self, app_id: str, app_secret: str) -> dict:
        """Expiry and granted scopes, via ``GET /debug_token``.

        Three things this had wrong before, all of them documented on
        developers.facebook.com and none of them guessable:

        * ``debug_token`` lives on graph.facebook.com. Calling it on
          graph.instagram.com — which is where an Instagram-Login client points
          — is a request to an endpoint that is not there.
        * the ``access_token`` parameter must be an **app** access token or a
          developer user token, not the token being examined. Passing the
          subject token as its own authority is what the old code did.
        * therefore the app id and secret are genuinely required *for this
          call*, and for nothing else in this client.

        The secret is registered with the log redactor before it is used and is
        never returned, printed or stored.
        """
        if not (app_id and app_secret):
            raise PublishError(
                "debug_token richiede META_APP_ID e META_APP_SECRET: sono le "
                "sole credenziali applicative necessarie, e servono solo qui",
                retryable=False, code="APP_CREDENTIALS")
        register_secret(app_secret)
        url = f"https://{TOKEN_DEBUG_HOST}/{self.api_version}/debug_token"
        params = {"input_token": self.token,
                  "access_token": f"{app_id}|{app_secret}"}
        try:
            r = self._s.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise PublishError(f"network error: {e}", retryable=True) from e
        return self._parse(r)
