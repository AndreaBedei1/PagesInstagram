"""In-memory mock of GraphClient for tests, dry-run and test mode.

Mirrors the GraphClient surface and can inject transient failures, container
errors and rate limits so the publisher's retry/idempotency logic is exercised
without any network.
"""
from __future__ import annotations

import os

from ..core.errors import PublishError


class MockGraphClient:
    def __init__(self, *, status_sequence: list[str] | None = None,
                 create_fail_times: int = 0, rate_limited: bool = False,
                 container_error: bool = False, upload_fail_times: int = 0,
                 upload_partial_bytes: int | None = None, account_type: str = "business",
                 already_published_on_publish: bool = False,
                 token_days: int = 59, scopes: tuple[str, ...] | None = None):
        from ..core.meta_api import REQUIRED_PERMISSIONS
        self.token_days = token_days
        self.scopes = REQUIRED_PERMISSIONS if scopes is None else scopes
        self.status_sequence = status_sequence or ["IN_PROGRESS", "FINISHED"]
        self.create_fail_times = create_fail_times
        self.rate_limited = rate_limited
        self.container_error = container_error
        self.upload_fail_times = upload_fail_times
        self.upload_partial_bytes = upload_partial_bytes
        self.account_type = account_type
        self.already_published_on_publish = already_published_on_publish
        self.containers: dict[str, dict] = {}
        self.published: dict[str, str] = {}
        self._n = 0
        self._create_fails = 0
        self._upload_fails = 0
        self._interrupted = False
        self.calls: list[tuple] = []

    def create_media_container(self, ig_user_id: str, *, media_type: str,
                               image_url: str | None = None,
                               video_url: str | None = None,
                               caption: str | None = None, **extra) -> str:
        self.calls.append(("create", media_type, image_url or video_url))
        if self.rate_limited:
            raise PublishError("mock rate limit", retryable=True, code="4")
        if self._create_fails < self.create_fail_times:
            self._create_fails += 1
            raise PublishError("mock transient", retryable=True, code="2")
        self._n += 1
        cid = f"mock_container_{self._n}"
        self.containers[cid] = {"polls": 0, "media_type": media_type}
        return cid

    def get_container_status(self, container_id: str) -> str:
        c = self.containers.get(container_id)
        if not c:
            return "ERROR"
        if self.container_error:
            return "ERROR"
        i = min(c["polls"], len(self.status_sequence) - 1)
        c["polls"] += 1
        return self.status_sequence[i]

    def publish_container(self, ig_user_id: str, creation_id: str) -> str:
        self.calls.append(("publish", creation_id))
        self._n += 1
        mid = f"mock_media_{self._n}"
        self.published[mid] = creation_id
        return mid

    def get_publishing_limit(self, ig_user_id: str) -> dict:
        return {"data": [{"quota_usage": len(self.published),
                          "config": {"quota_total": 100, "quota_duration": 86400}}]}

    def verify_token(self) -> dict:
        self.calls.append(("verify_token",))
        return {"user_id": "ig", "username": "mock_user",
                "account_type": self.account_type.upper()}

    def debug_token(self, app_id: str, app_secret: str) -> dict:
        """Same signature as the real client: app credentials, or nothing."""
        self.calls.append(("debug_token",))
        if not (app_id and app_secret):
            raise PublishError("debug_token richiede META_APP_ID e META_APP_SECRET",
                               retryable=False, code="APP_CREDENTIALS")
        import time
        return {"data": {"is_valid": True,
                         "expires_at": int(time.time()) + self.token_days * 86400,
                         "scopes": list(self.scopes)}}

    # -- resumable ---------------------------------------------------------
    def create_resumable_container(self, ig_user_id: str, *, media_type: str,
                                   caption: str | None = None,
                                   share_to_feed: bool | None = None, **extra) -> tuple[str, str]:
        self.calls.append(("create_resumable", media_type, share_to_feed, caption))
        if self.rate_limited:
            raise PublishError("mock rate limit", retryable=True, code="4")
        if self._create_fails < self.create_fail_times:
            self._create_fails += 1
            raise PublishError("mock transient", retryable=True, code="2")
        self._n += 1
        cid = f"mock_container_{self._n}"
        uri = f"https://rupload.facebook.com/ig-api-upload/v25.0/{cid}"
        self.containers[cid] = {"polls": 0, "media_type": media_type,
                                "transferred": 0, "size": None}
        return cid, uri

    def upload_video_file(self, upload_uri: str, file_path: str, *, offset: int = 0,
                          timeout: int | None = None) -> dict:
        self.calls.append(("upload", offset))
        cid = upload_uri.rstrip("/").rsplit("/", 1)[1]
        c = self.containers.setdefault(cid, {"transferred": 0, "size": None})
        size = os.path.getsize(file_path)
        c["size"] = size
        if self._upload_fails < self.upload_fail_times:
            self._upload_fails += 1
            raise PublishError("mock upload transient", retryable=True, code="2")
        # Simulate a mid-upload interruption exactly once, then let resume finish.
        if (self.upload_partial_bytes is not None and not self._interrupted
                and offset < self.upload_partial_bytes):
            c["transferred"] = self.upload_partial_bytes
            self._interrupted = True
            raise PublishError("mock connection interrupted", retryable=True, code="AMBIGUOUS")
        c["transferred"] = size
        return {"success": True, "message": "Upload successful."}

    def get_upload_status(self, container_id: str) -> dict:
        c = self.containers.get(container_id, {})
        size, xfer = c.get("size"), c.get("transferred", 0)
        done = size is not None and xfer >= size
        return {"status_code": "FINISHED" if done else "IN_PROGRESS",
                "bytes_transferred": xfer}

    def get_account_info(self, ig_user_id: str) -> dict:
        return {"user_id": ig_user_id, "username": "mock_user",
                "account_type": self.account_type.upper()}
