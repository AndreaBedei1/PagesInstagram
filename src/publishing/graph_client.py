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

import requests

from ..core.errors import PublishError
from ..core.logging_setup import get_logger

log = get_logger("publishing.graph")

# Meta error codes that are transient/worth retrying.
_RETRYABLE_CODES = {1, 2, 4, 17, 32, 341, 613}  # rate limit / temporary
# Codes that mean the token/permission is broken (do NOT retry blindly).
_AUTH_CODES = {190, 102, 10, 200, 803}


class GraphClient:
    def __init__(self, access_token: str, *, api_version: str = "v23.0",
                 flavor: str = "instagram_login", session: requests.Session | None = None,
                 timeout: int = 60):
        self.token = access_token
        self.api_version = api_version
        host = "graph.instagram.com" if flavor == "instagram_login" else "graph.facebook.com"
        self.base = f"https://{host}/{api_version}"
        self.timeout = timeout
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

    def get_publishing_limit(self, ig_user_id: str) -> dict:
        return self._request("GET", f"{ig_user_id}/content_publishing_limit",
                             fields="config,quota_usage")

    def debug_token(self) -> dict:
        """Best-effort token info (validity/expiry). Non-fatal on error."""
        try:
            return self._request("GET", "debug_token", input_token=self.token)
        except PublishError as e:
            log.warning("debug_token failed: %s", e)
            return {}
