"""A public HTTPS URL that exists for one publication and then stops existing.

Meta's Instagram content publishing wants ``video_url``: a URL it can fetch the
video from. The alternative — ``upload_type=resumable``, streaming the file
straight to ``rupload.facebook.com`` — is documented only for Facebook Login
apps and, on this account, answers ``ProcessingFailedError`` for every file and
every HTTP client, with zero bytes accepted. Verified with curl in the exact
documented form, with the original media and with a conservatively re-encoded
one. So ``video_url`` it is.

That normally means paying for hosting: a bucket, a CDN, a VPS, a card on file.
It does not have to. This serves the single file from ``127.0.0.1`` and lets a
Cloudflare Quick Tunnel put an HTTPS address in front of it for the ninety-odd
seconds Meta needs to fetch it. No Cloudflare account, no domain, no DNS, no
payment method, nothing left running afterwards.

The design constraints follow from what is being exposed — a public URL into a
machine holding tokens, a database and five thousand items of a corpus:

* the server knows **one** file and **one** path, and answers 404 to everything
  else. Not "the directory has only one file in it": there is no directory
  traversal to get wrong, because there is no directory.
* the path is a fresh UUID each time, so an address that leaks is useless the
  moment the publication ends.
* the bind is ``127.0.0.1``. The tunnel is the only route in, and it dies with
  the process.
* one tunnel at a time across the whole project, held by an OS-level lock that
  the kernel releases if the process dies.
* cleanup runs in ``finally``, and verifies the processes are actually gone.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from ..core.errors import PublishError
from ..core.logging_setup import get_logger

log = get_logger("publishing.quick_tunnel")

#: Official Cloudflare release. Downloaded once into a gitignored runtime
#: directory; never committed, never installed system-wide, never re-fetched
#: for a publication that already has it.
CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
                   "download/cloudflared-windows-amd64.exe")

TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

#: What Meta is told the bytes are. Guessed from the extension rather than
#: assumed to be video: a still image post is now the main format, and serving
#: a PNG as video/mp4 would fail in a way that reads like a network problem.
CONTENT_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime",
                 ".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg"}


def content_type_for(path) -> str:
    return CONTENT_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")

#: Paths that must never resolve, checked against the live tunnel before Meta
#: is told the URL exists. Not a formality: this is the whole security argument.
FORBIDDEN_PATHS = ("/", "/.env", "/.git/", "/database/content.sqlite",
                   "/stories/", "/generated/", "/accounts/")

DEFAULT_STARTUP_TIMEOUT = 45.0


# ---------------------------------------------------------------- the server
class _OneFileHandler(BaseHTTPRequestHandler):
    """Answers exactly one path. Everything else is 404, including ``/``."""

    server_version = "ice-oneshot/1.0"
    sys_version = ""

    # Set by the server instance.
    file_path: Path = Path()
    route: str = ""
    size: int = 0
    content_type: str = "application/octet-stream"

    def log_message(self, fmt, *args):
        log.debug("one-file %s %s", self.address_string(), fmt % args)

    def _not_found(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorised(self) -> bool:
        return self.path.split("?", 1)[0] == self.route

    def do_HEAD(self):  # noqa: N802 - stdlib naming
        if not self._authorised():
            return self._not_found()
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(self.size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - stdlib naming
        if not self._authorised():
            return self._not_found()
        start, end, partial = 0, self.size - 1, False
        header = self.headers.get("Range")
        if header and header.startswith("bytes="):
            spec = header[6:].split(",")[0]
            first, _, last = spec.partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else self.size - 1
                else:
                    start = max(0, self.size - int(last))
                partial = True
            except ValueError:
                start, end, partial = 0, self.size - 1, False
        end = min(end, self.size - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{self.size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{self.size}")
        self.end_headers()
        with open(self.file_path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(262_144, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return  # the fetcher hung up; nothing to salvage
                remaining -= len(chunk)


def free_port() -> int:
    """A port the OS says is free, right now, on the loopback interface."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class OneFileServer:
    """Serves one file at one unguessable path, on localhost only."""

    def __init__(self, file_path: str | Path, *, port: int | None = None,
                 route: str | None = None):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise PublishError(f"media assente: {self.file_path}",
                               retryable=False, code="FILE_MISSING")
        self.size = self.file_path.stat().st_size
        # A fresh UUID per publication: an address that leaks is worthless the
        # moment the tunnel closes, and it never encodes the job, the page, the
        # date or the real filename.
        self.content_type = content_type_for(self.file_path)
        suffix = self.file_path.suffix.lower() or ".bin"
        self.route = route or f"/media/{uuid.uuid4()}{suffix}"
        self.port = port or free_port()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.route}"

    def start(self) -> "OneFileServer":
        handler = type("_Bound", (_OneFileHandler,),
                       {"file_path": self.file_path, "route": self.route,
                        "size": self.size, "content_type": self.content_type})
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="one-file-server", daemon=True)
        self._thread.start()
        log.info("one-file server su 127.0.0.1:%s (%s byte)", self.port, self.size)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    @property
    def alive(self) -> bool:
        return self._server is not None and bool(self._thread and self._thread.is_alive())

    def __enter__(self) -> "OneFileServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


# ------------------------------------------------------------- cloudflared
def runtime_dir(paths) -> Path:
    d = Path(paths.root) / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_cloudflared(paths=None) -> Path | None:
    """Already on this machine? PATH first, then the project's runtime dir."""
    import shutil

    found = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if found:
        return Path(found)
    if paths is not None:
        candidate = runtime_dir(paths) / ("cloudflared.exe" if os.name == "nt"
                                          else "cloudflared")
        if candidate.exists():
            return candidate
    return None


def ensure_cloudflared(paths, *, download: bool = True) -> Path:
    """The binary, fetched once from Cloudflare's own releases if missing.

    Not committed — it is 50 MB of third-party executable — and not installed
    system-wide, so nothing outside this project changes and no administrator
    rights are needed.
    """
    existing = find_cloudflared(paths)
    if existing:
        return existing
    if not download:
        raise PublishError(
            "cloudflared non disponibile: installalo o consenti il download",
            retryable=False, code="NO_CLOUDFLARED")
    target = runtime_dir(paths) / ("cloudflared.exe" if os.name == "nt"
                                   else "cloudflared")
    log.info("scarico cloudflared da %s", CLOUDFLARED_URL)
    try:
        with requests.get(CLOUDFLARED_URL, stream=True, timeout=300) as r:
            r.raise_for_status()
            tmp = target.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            tmp.replace(target)
    except requests.RequestException as e:
        raise PublishError(f"download di cloudflared non riuscito: {e}",
                           retryable=True, code="CLOUDFLARED_DOWNLOAD") from e
    if target.stat().st_size < 5_000_000:      # a real build is tens of MB
        target.unlink(missing_ok=True)
        raise PublishError("cloudflared scaricato incompleto", retryable=True,
                           code="CLOUDFLARED_DOWNLOAD")
    if os.name != "nt":
        target.chmod(0o755)
    return target


def parse_tunnel_url(text: str) -> str | None:
    """The ``https://<random>.trycloudflare.com`` cloudflared prints on startup."""
    match = TRYCLOUDFLARE_RE.search(text or "")
    return match.group(0) if match else None


class QuickTunnel:
    """One ``cloudflared tunnel --url`` subprocess, and its public hostname."""

    def __init__(self, binary: str | Path, local_port: int, *,
                 startup_timeout: float = DEFAULT_STARTUP_TIMEOUT):
        self.binary = Path(binary)
        self.local_port = local_port
        self.startup_timeout = startup_timeout
        self.public_url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._output: list[str] = []
        self._reader: threading.Thread | None = None

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            self._output.append(line)
            if self.public_url is None:
                found = parse_tunnel_url(line)
                if found:
                    self.public_url = found

    def start(self) -> str:
        self._proc = subprocess.Popen(
            [str(self.binary), "tunnel", "--no-autoupdate",
             "--url", f"http://127.0.0.1:{self.local_port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._reader = threading.Thread(target=self._pump, name="cloudflared-out",
                                        daemon=True)
        self._reader.start()

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.public_url:
                log.info("quick tunnel attivo")
                return self.public_url
            if self._proc.poll() is not None:
                break
            time.sleep(0.4)
        tail = "".join(self._output[-8:])
        self.stop()
        raise PublishError(
            f"cloudflared non ha prodotto un URL entro {self.startup_timeout:.0f}s: "
            f"{tail.strip()[:300]}", retryable=True, code="TUNNEL_STARTUP")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                log.warning("cloudflared non ha risposto a terminate: kill")
                proc.kill()
                proc.wait(timeout=10)
        if proc.stdout:
            try:
                proc.stdout.close()
            except OSError:
                pass
        if self._reader:
            self._reader.join(timeout=5)
            self._reader = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def __enter__(self) -> "QuickTunnel":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


# ------------------------------------------------------------- health checks
@dataclass
class TunnelHealth:
    ok: bool = False
    checks: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


#: Seconds to wait before the *first* probe of a brand-new tunnel hostname.
#:
#: Not politeness — correctness. The name does not exist in DNS for a few
#: seconds after cloudflared announces it, and a resolver that is asked too
#: early caches the NXDOMAIN. On Windows that cache then answers every
#: subsequent lookup for the length of its TTL, so an eager first probe does not
#: merely fail, it poisons the retries: measured as forty-eight seconds of
#: uninterrupted "host not found" on a hostname that resolved fine from
#: nslookup at the same moment.
FIRST_PROBE_DELAY = 12.0

#: Registration is not routing. Once DNS answers, the edge still returns 530 —
#: origin unreachable — until it has a route to the tunnel. Around seventy
#: seconds end to end has been typical here.
EDGE_READY_TIMEOUT = 240.0


def wait_until_reachable(url: str, *, timeout: float = EDGE_READY_TIMEOUT,
                         first_delay: float = FIRST_PROBE_DELAY,
                         session=None, sleep=time.sleep) -> bool:
    """Wait for the edge to catch up with the tunnel it just announced."""
    http = session or requests
    sleep(first_delay)
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            r = http.head(url, timeout=15)
            if r.status_code == 200:
                log.info("edge pronto dopo %s tentativi", attempt)
                return True
            if r.status_code not in (429, 500, 502, 503, 504, 530):
                return False        # a real answer, just not the one we want
        except requests.RequestException:
            pass
        sleep(4.0)
    log.warning("edge non pronto dopo %.0fs (%s tentativi)", timeout, attempt)
    return False


def check_public_url(url: str, expected_size: int, *,
                     expected_type: str = "video/mp4",
                     timeout: float = 45.0, session=None) -> TunnelHealth:
    """Prove the URL works, and prove nothing else does — before telling Meta.

    A tunnel that answers 200 on the media and also serves ``/.env`` would be a
    catastrophe discovered after the fact. Both halves are checked here, and a
    failure means Meta is never given the address at all.
    """
    http = session or requests
    health = TunnelHealth()
    base = url.rsplit("/media/", 1)[0] if "/media/" in url else url

    try:
        r = http.get(url, timeout=timeout, stream=True)
        body = len(r.content)
        health.checks["GET"] = r.status_code
        health.checks["content_type"] = r.headers.get("Content-Type")
        health.checks["content_length"] = r.headers.get("Content-Length")
        health.checks["bytes"] = body
        if r.status_code != 200:
            health.problems.append(f"GET {r.status_code}, atteso 200")
        if (r.headers.get("Content-Type") or "").split(";")[0] != expected_type:
            health.problems.append(
                f"Content-Type {r.headers.get('Content-Type')!r}, "
                f"atteso {expected_type}")
        if body != expected_size:
            health.problems.append(
                f"{body} byte serviti, {expected_size} attesi")

        h = http.head(url, timeout=timeout)
        health.checks["HEAD"] = h.status_code
        if h.status_code != 200:
            health.problems.append(f"HEAD {h.status_code}, atteso 200")

        rng = http.get(url, headers={"Range": "bytes=0-1023"}, timeout=timeout)
        health.checks["RANGE"] = rng.status_code
        if rng.status_code != 206:
            health.problems.append(f"Range {rng.status_code}, atteso 206")
        elif len(rng.content) != 1024:
            health.problems.append(f"Range ha reso {len(rng.content)} byte, attesi 1024")
    except requests.RequestException as e:
        health.problems.append(f"URL pubblico irraggiungibile: {e}")
        return health

    for path in FORBIDDEN_PATHS:
        try:
            probe = http.get(base.rstrip("/") + path, timeout=timeout)
            health.checks[f"404{path}"] = probe.status_code
            if probe.status_code != 404:
                health.problems.append(
                    f"{path} risponde {probe.status_code}: deve essere 404")
        except requests.RequestException:
            pass  # unreachable is at least not exposed

    health.ok = not health.problems
    return health


# ------------------------------------------------------------------- session
class TunnelSession:
    """Server + tunnel + verified public URL, for the length of one publication.

    Used as a context manager so the teardown is structural rather than
    remembered: whatever happens between ``__enter__`` and ``__exit__`` — a
    Meta error, a timeout, a KeyboardInterrupt — the processes are stopped and
    the lock is released.
    """

    def __init__(self, media_path: str | Path, paths, *,
                 startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
                 download_cloudflared: bool = True, lock_path=None):
        self.media_path = Path(media_path)
        self.paths = paths
        self.startup_timeout = startup_timeout
        self.download_cloudflared = download_cloudflared
        self.lock_path = Path(lock_path) if lock_path else (
            Path(paths.logs) / "quick_tunnel.lock")
        self.server: OneFileServer | None = None
        self.tunnel: QuickTunnel | None = None
        self.public_url: str | None = None
        self.health: TunnelHealth | None = None
        self._lock = None

    def __enter__(self) -> "TunnelSession":
        from ..scheduling.lock import SingleInstanceLock

        # One tunnel at a time for the whole project. The OS releases this if
        # the holder dies, so a stale file from a crashed run cannot wedge the
        # next publication — the lock is the kernel's, not the file's contents.
        self._lock = SingleInstanceLock(self.lock_path)
        if not self._lock.acquire():
            self._lock = None
            raise PublishError(
                "un altro Quick Tunnel è già attivo: le pubblicazioni sono "
                "serializzate di proposito", retryable=True, code="TUNNEL_BUSY")
        try:
            binary = ensure_cloudflared(self.paths,
                                        download=self.download_cloudflared)
            self.server = OneFileServer(self.media_path).start()
            self.tunnel = QuickTunnel(binary, self.server.port,
                                      startup_timeout=self.startup_timeout)
            host = self.tunnel.start()
            self.public_url = host.rstrip("/") + self.server.route
            if not wait_until_reachable(self.public_url):
                raise PublishError(
                    "l'edge Cloudflare non ha instradato il tunnel entro il "
                    "tempo previsto", retryable=True, code="TUNNEL_EDGE")
            self.health = check_public_url(self.public_url, self.server.size,
                                           expected_type=self.server.content_type)
            if not self.health.ok:
                raise PublishError(
                    "il tunnel non ha superato i controlli: "
                    + "; ".join(self.health.problems),
                    retryable=True, code="TUNNEL_HEALTH")
            log.info("URL pubblico verificato (%s byte)", self.server.size)
            return self
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Stop everything, in reverse order, and never raise from cleanup."""
        for name, stop in (("cloudflared", getattr(self.tunnel, "stop", None)),
                           ("server", getattr(self.server, "stop", None))):
            if stop is None:
                continue
            try:
                stop()
            except Exception as e:  # noqa: BLE001 - cleanup must not mask the cause
                log.warning("errore fermando %s: %s", name, e)
        if self.tunnel and self.tunnel.alive:
            log.error("cloudflared risulta ancora vivo dopo lo stop")
        elif self.public_url:
            # A clean teardown used to leave no trace at all, which makes "did
            # it really close?" a question the log cannot answer. It can now.
            log.info("tunnel chiuso: cloudflared e server fermi, URL non più "
                     "raggiungibile")
        self.tunnel = None
        self.server = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def __exit__(self, *exc) -> None:
        self.close()
