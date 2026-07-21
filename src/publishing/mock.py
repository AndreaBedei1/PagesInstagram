"""In-memory mock of GraphClient for tests, dry-run and test mode.

Mirrors the GraphClient surface and can inject transient failures, container
errors and rate limits so the publisher's retry/idempotency logic is exercised
without any network.
"""
from __future__ import annotations

from ..core.errors import PublishError


class MockGraphClient:
    def __init__(self, *, status_sequence: list[str] | None = None,
                 create_fail_times: int = 0, rate_limited: bool = False,
                 container_error: bool = False):
        self.status_sequence = status_sequence or ["IN_PROGRESS", "FINISHED"]
        self.create_fail_times = create_fail_times
        self.rate_limited = rate_limited
        self.container_error = container_error
        self.containers: dict[str, dict] = {}
        self.published: dict[str, str] = {}
        self._n = 0
        self._create_fails = 0
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

    def debug_token(self) -> dict:
        return {"data": {"is_valid": True, "expires_at": 0, "scopes": []}}
