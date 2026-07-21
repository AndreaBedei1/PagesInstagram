"""HTTP client for a local ComfyUI instance (official /prompt, /history, /view API).

No websockets required — the client polls /history for completion. It can also
auto-start ComfyUI via its launcher and wait until the server is ready.
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from dataclasses import dataclass

import requests

from ..core.errors import ComfyUIError
from ..core.logging_setup import get_logger

log = get_logger("comfyui.client")


@dataclass
class ImageRef:
    filename: str
    subfolder: str
    type: str


class ComfyUIClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        *,
        request_timeout: int = 120,
        poll_interval: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval
        self.client_id = uuid.uuid4().hex
        self._session = requests.Session()

    # -- readiness / lifecycle ---------------------------------------------
    def is_ready(self) -> bool:
        try:
            r = self._session.get(f"{self.base_url}/system_stats", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def wait_until_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_ready():
                return True
            time.sleep(min(2.0, self.poll_interval))
        return False

    def ensure_running(self, launch_command: str | None, startup_timeout: float) -> bool:
        """Return True if ComfyUI is reachable, auto-starting it if configured."""
        if self.is_ready():
            return True
        if not launch_command:
            return False
        if not os.path.exists(launch_command):
            log.warning("ComfyUI launch command not found: %s", launch_command)
            return False
        log.info("Starting ComfyUI via %s", launch_command)
        try:
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
            subprocess.Popen(
                launch_command,
                cwd=os.path.dirname(launch_command) or None,
                shell=True,
                creationflags=flags,
            )
        except Exception as e:  # noqa: BLE001
            raise ComfyUIError(f"failed to launch ComfyUI: {e}") from e
        return self.wait_until_ready(startup_timeout)

    # -- generation --------------------------------------------------------
    def queue_prompt(self, graph: dict) -> str:
        payload = {"prompt": graph, "client_id": self.client_id}
        try:
            r = self._session.post(
                f"{self.base_url}/prompt", json=payload, timeout=self.request_timeout
            )
        except requests.RequestException as e:
            raise ComfyUIError(f"queue_prompt failed: {e}") from e
        if r.status_code != 200:
            raise ComfyUIError(f"queue_prompt HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"no prompt_id in response: {data}")
        return prompt_id

    def _history(self, prompt_id: str) -> dict | None:
        try:
            r = self._session.get(
                f"{self.base_url}/history/{prompt_id}", timeout=self.request_timeout
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return data.get(prompt_id)
        except requests.RequestException:
            return None

    def wait_for_images(self, prompt_id: str, timeout: float) -> list[ImageRef]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entry = self._history(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyUIError(f"generation error: {status}")
                images = self._extract_images(entry)
                if images:
                    return images
                if status.get("completed"):
                    raise ComfyUIError("prompt completed but produced no images")
            time.sleep(self.poll_interval)
        raise ComfyUIError(f"timeout waiting for prompt {prompt_id}")

    @staticmethod
    def _extract_images(entry: dict) -> list[ImageRef]:
        images: list[ImageRef] = []
        for node_out in (entry.get("outputs") or {}).values():
            for img in node_out.get("images", []) or []:
                images.append(
                    ImageRef(
                        filename=img.get("filename", ""),
                        subfolder=img.get("subfolder", ""),
                        type=img.get("type", "output"),
                    )
                )
        return images

    def fetch_image(self, ref: ImageRef) -> bytes:
        params = {"filename": ref.filename, "subfolder": ref.subfolder, "type": ref.type}
        try:
            r = self._session.get(
                f"{self.base_url}/view", params=params, timeout=self.request_timeout
            )
        except requests.RequestException as e:
            raise ComfyUIError(f"fetch_image failed: {e}") from e
        if r.status_code != 200:
            raise ComfyUIError(f"fetch_image HTTP {r.status_code}")
        return r.content

    def generate(self, graph: dict, *, timeout: float) -> bytes:
        """Queue a graph, wait for the first output image, return its bytes."""
        prompt_id = self.queue_prompt(graph)
        log.info("ComfyUI prompt queued: %s", prompt_id)
        images = self.wait_for_images(prompt_id, timeout)
        return self.fetch_image(images[0])
