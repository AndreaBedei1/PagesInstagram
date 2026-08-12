"""The tunnel that exists for one publication, and the server behind it.

This is the piece that puts a public HTTPS address in front of a machine
holding tokens, a database and the whole corpus. Most of what follows is
therefore about what the server refuses to serve, which is everything except
one file at one unguessable path.

Nothing here starts cloudflared or reaches the network: the tunnel process is
faked, and the server is exercised over real HTTP against localhost, which is
where it lives anyway.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import requests

from src.core.errors import PublishError
from src.publishing.quick_tunnel import (FORBIDDEN_PATHS, OneFileServer,
                                         QuickTunnel, TunnelHealth,
                                         check_public_url, free_port,
                                         parse_tunnel_url)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    f = tmp_path / "reel.mp4"
    f.write_bytes(b"\x00\x11\x22\x33" * 4096)      # 16 KiB of stand-in video
    return f


@pytest.fixture
def server(media: Path):
    s = OneFileServer(media).start()
    yield s
    s.stop()


# ---- the one file it will serve --------------------------------------------
def test_get_returns_the_file_with_the_right_headers(server, media):
    r = requests.get(server.local_url, timeout=10)
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "video/mp4"
    assert int(r.headers["Content-Length"]) == media.stat().st_size
    assert r.headers["Accept-Ranges"] == "bytes"
    assert r.content == media.read_bytes()


def test_head_answers_without_a_body(server, media):
    r = requests.head(server.local_url, timeout=10)
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "video/mp4"
    assert int(r.headers["Content-Length"]) == media.stat().st_size
    assert not r.content


def test_range_returns_206_and_exactly_the_bytes_asked_for(server, media):
    r = requests.get(server.local_url, headers={"Range": "bytes=0-1023"}, timeout=10)
    assert r.status_code == 206
    assert len(r.content) == 1024
    assert r.content == media.read_bytes()[:1024]
    assert r.headers["Content-Range"].endswith(f"/{media.stat().st_size}")


def test_a_suffix_range_works_too(server, media):
    r = requests.get(server.local_url, headers={"Range": "bytes=-512"}, timeout=10)
    assert r.status_code == 206
    assert r.content == media.read_bytes()[-512:]


def test_an_unsatisfiable_range_is_416(server):
    r = requests.get(server.local_url, headers={"Range": "bytes=999999999-"},
                     timeout=10)
    assert r.status_code == 416


# ---- and everything it will not ---------------------------------------------
@pytest.mark.parametrize("path", list(FORBIDDEN_PATHS))
def test_the_paths_that_must_never_resolve(server, path):
    base = server.local_url.rsplit("/media/", 1)[0]
    r = requests.get(base + path, timeout=10)
    assert r.status_code == 404, f"{path} ha risposto {r.status_code}"


def test_the_real_filename_is_not_a_route(server, media):
    """The address is the UUID, not the file. Knowing the name buys nothing."""
    base = server.local_url.rsplit("/media/", 1)[0]
    for guess in (f"/{media.name}", f"/media/{media.name}", "/reel.mp4",
                  "/media/", "/media"):
        assert requests.get(base + guess, timeout=10).status_code == 404


def test_traversal_does_not_escape(server, tmp_path):
    base = server.local_url.rsplit("/media/", 1)[0]
    for attack in ("/../../../.env", "/media/../../.env", "/%2e%2e/%2e%2e/.env"):
        assert requests.get(base + attack, timeout=10).status_code == 404


def test_the_route_is_a_fresh_uuid_every_time(media):
    routes = set()
    for _ in range(5):
        s = OneFileServer(media)
        routes.add(s.route)
        assert s.route.startswith("/media/") and s.route.endswith(".mp4")
    assert len(routes) == 5, "un path riutilizzato sopravvive alla pubblicazione"


def test_the_route_never_encodes_the_job_or_the_filename(media):
    s = OneFileServer(media)
    assert media.stem not in s.route


def test_it_binds_only_to_loopback(server):
    """Not 0.0.0.0: the tunnel is the only way in, and it dies with the process."""
    with socket.socket() as probe:
        probe.settimeout(2)
        assert probe.connect_ex(("127.0.0.1", server.port)) == 0
    host = socket.gethostbyname(socket.gethostname())
    if host.startswith("127."):
        pytest.skip("questa macchina non ha un indirizzo LAN da provare")
    with socket.socket() as probe:
        probe.settimeout(2)
        assert probe.connect_ex((host, server.port)) != 0, (
            "il server risponde su un'interfaccia non di loopback")


def test_a_missing_media_is_refused_before_anything_opens(tmp_path):
    with pytest.raises(PublishError) as e:
        OneFileServer(tmp_path / "assente.mp4")
    assert e.value.code == "FILE_MISSING"


def test_free_port_gives_something_bindable():
    port = free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_stop_really_releases_the_port(media):
    s = OneFileServer(media).start()
    port = s.port
    assert s.alive
    s.stop()
    assert not s.alive
    with socket.socket() as probe:      # the port is free again
        probe.bind(("127.0.0.1", port))


# ---- parsing what cloudflared prints ----------------------------------------
def test_the_url_is_found_in_a_real_looking_banner():
    banner = (
        "2026-08-12T09:14:02Z INF +--------------------------------------------+\n"
        "2026-08-12T09:14:02Z INF |  Your quick Tunnel has been created!       |\n"
        "2026-08-12T09:14:02Z INF |  https://tab-mean-analyzed-join.trycloudflare.com |\n"
        "2026-08-12T09:14:02Z INF +--------------------------------------------+\n")
    assert parse_tunnel_url(banner) == "https://tab-mean-analyzed-join.trycloudflare.com"


@pytest.mark.parametrize("text", [
    "", "nessun url qui",
    "https://example.com/not-a-tunnel",
    "http://plain-http.trycloudflare.com",          # must be https
    "trycloudflare.com senza schema",
])
def test_nothing_else_is_mistaken_for_a_tunnel_url(text):
    assert parse_tunnel_url(text) is None


# ---- startup and teardown ----------------------------------------------------
class _FakeCloudflared:
    """A stand-in binary: a python script printing what cloudflared prints."""

    @staticmethod
    def script(tmp_path: Path, body: str) -> Path:
        p = tmp_path / "fake_cloudflared.py"
        p.write_text(body, encoding="utf-8")
        return p


def _tunnel_with(tmp_path, body, **kw):
    import sys
    script = _FakeCloudflared.script(tmp_path, body)
    t = QuickTunnel(sys.executable, 1234, **kw)
    t.binary = Path(sys.executable)
    original = t.start

    def start_with_script():
        import subprocess
        t._proc = subprocess.Popen(
            [sys.executable, str(script)], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1)
        t._reader = threading.Thread(target=t._pump, daemon=True)
        t._reader.start()
        deadline = time.monotonic() + t.startup_timeout
        while time.monotonic() < deadline:
            if t.public_url:
                return t.public_url
            if t._proc.poll() is not None:
                break
            time.sleep(0.05)
        t.stop()
        raise PublishError("timeout", retryable=True, code="TUNNEL_STARTUP")

    t.start = start_with_script
    return t


def test_startup_captures_the_url_and_the_process_stays_alive(tmp_path):
    t = _tunnel_with(tmp_path, "import time\n"
                               "print('INF |  https://abc-def.trycloudflare.com |', flush=True)\n"
                               "time.sleep(30)\n")
    try:
        url = t.start()
        assert url == "https://abc-def.trycloudflare.com"
        assert t.alive
    finally:
        t.stop()
    assert not t.alive


def test_a_tunnel_that_never_prints_a_url_times_out_and_is_retryable(tmp_path):
    t = _tunnel_with(tmp_path, "import time\ntime.sleep(30)\n", startup_timeout=1.0)
    with pytest.raises(PublishError) as e:
        t.start()
    assert e.value.retryable and e.value.code == "TUNNEL_STARTUP"
    assert not t.alive, "un avvio fallito non deve lasciare un processo orfano"


def test_a_tunnel_that_dies_immediately_is_retryable(tmp_path):
    t = _tunnel_with(tmp_path, "raise SystemExit(1)\n", startup_timeout=8.0)
    with pytest.raises(PublishError) as e:
        t.start()
    assert e.value.retryable
    assert not t.alive


def test_stop_is_safe_to_call_twice(tmp_path):
    t = _tunnel_with(tmp_path, "import time\n"
                               "print('https://x-y.trycloudflare.com', flush=True)\n"
                               "time.sleep(30)\n")
    t.start()
    t.stop()
    t.stop()
    assert not t.alive


# ---- the health gate ---------------------------------------------------------
class _FakeHTTP:
    """Answers whatever the test says, for the media URL and the forbidden ones."""

    def __init__(self, *, status=200, ctype="video/mp4", body=b"x" * 100,
                 head=200, rng=206, forbidden=404):
        self.status, self.ctype, self.body = status, ctype, body
        self.head_status, self.rng, self.forbidden = head, rng, forbidden

    class _R:
        def __init__(self, status, headers, content):
            self.status_code, self.headers, self.content = status, headers, content

    def get(self, url, timeout=None, headers=None, stream=None):
        if "/media/" not in url:
            return self._R(self.forbidden, {}, b"")
        if headers and "Range" in headers:
            return self._R(self.rng, {}, self.body[:1024].ljust(1024, b"\0")
                           if self.rng == 206 else b"")
        return self._R(self.status, {"Content-Type": self.ctype,
                                     "Content-Length": str(len(self.body))},
                       self.body)

    def head(self, url, timeout=None):
        return self._R(self.head_status, {}, b"")


URL = "https://abc.trycloudflare.com/media/1234.mp4"


def test_a_healthy_tunnel_passes():
    body = b"x" * 4096
    health = check_public_url(URL, len(body), session=_FakeHTTP(body=body))
    assert health.ok, health.problems


@pytest.mark.parametrize("kwargs,fragment", [
    ({"status": 502}, "GET 502"),
    ({"ctype": "text/html"}, "Content-Type"),
    ({"head": 500}, "HEAD 500"),
    ({"rng": 200}, "Range 200"),
    ({"forbidden": 200}, "deve essere 404"),
])
def test_every_failing_check_is_reported_and_blocks(kwargs, fragment):
    body = b"x" * 4096
    health = check_public_url(URL, len(body), session=_FakeHTTP(body=body, **kwargs))
    assert not health.ok
    assert any(fragment in p for p in health.problems), health.problems


def test_a_size_mismatch_blocks():
    health = check_public_url(URL, 999999, session=_FakeHTTP(body=b"x" * 10))
    assert not health.ok
    assert any("byte" in p for p in health.problems)


def test_an_unreachable_url_blocks_rather_than_passing_quietly():
    class Dead:
        def get(self, *a, **k):
            raise requests.ConnectionError("boom")

        def head(self, *a, **k):
            raise requests.ConnectionError("boom")

    health = check_public_url(URL, 10, session=Dead())
    assert not health.ok and health.problems


def test_health_is_false_by_default():
    assert TunnelHealth().ok is False


# ---- waiting for the edge ----------------------------------------------------
def test_the_first_probe_waits_before_asking_dns_anything():
    """Asking too early caches an NXDOMAIN that then answers every retry."""
    from src.publishing.quick_tunnel import FIRST_PROBE_DELAY, wait_until_reachable

    slept = []

    class Ready:
        def head(self, url, timeout=None):
            return type("R", (), {"status_code": 200})()

    assert wait_until_reachable("https://x.trycloudflare.com/media/a.mp4",
                                session=Ready(), sleep=slept.append)
    assert slept and slept[0] == FIRST_PROBE_DELAY


def test_530_is_waited_through_because_registration_is_not_routing():
    from src.publishing.quick_tunnel import wait_until_reachable

    class SlowEdge:
        def __init__(self):
            self.n = 0

        def head(self, url, timeout=None):
            self.n += 1
            code = 530 if self.n < 5 else 200
            return type("R", (), {"status_code": code})()

    edge = SlowEdge()
    assert wait_until_reachable("https://x.trycloudflare.com/media/a.mp4",
                                session=edge, sleep=lambda _s: None)
    assert edge.n == 5


def test_a_real_answer_that_is_not_200_stops_the_wait():
    from src.publishing.quick_tunnel import wait_until_reachable

    class Gone:
        def head(self, url, timeout=None):
            return type("R", (), {"status_code": 404})()

    assert not wait_until_reachable("https://x.trycloudflare.com/media/a.mp4",
                                    session=Gone(), sleep=lambda _s: None)


def test_the_wait_gives_up_rather_than_hanging_forever():
    from src.publishing.quick_tunnel import wait_until_reachable

    class Never:
        def head(self, url, timeout=None):
            return type("R", (), {"status_code": 530})()

    assert not wait_until_reachable("https://x.trycloudflare.com/media/a.mp4",
                                    timeout=0.05, first_delay=0,
                                    session=Never(), sleep=lambda _s: None)
