"""Reachability audit for the source URLs of the fact-checked datasets.

A formally valid URL proves nothing. The three fact-checked datasets were built
by deriving a URL from the subject (``it.wikipedia.org/wiki/<Titolo>``) or from
the lemma (``treccani.it/vocabolario/<lemma>/``), so a typo, a disambiguation
page or a renamed article all produce a URL that passes ``urlparse`` and leads
nowhere. This module actually asks the server.

What it checks per URL:

* HTTPS scheme and an allow-listed host;
* the HTTP response, following redirects, recording the final URL;
* 404 / 403 / 5xx / timeouts, each as its own outcome;
* soft-404s, where detectable (Wikipedia's "page does not exist" markers);
* duplicates across the dataset.

How it behaves towards the servers:

* an identifiable user agent;
* a per-host token bucket (default 4 requests/second);
* bounded retries with backoff, only for transient failures;
* a finite timeout on every request — no unbounded wait, no infinite loop;
* an on-disk cache, so a re-run only asks about URLs it has not seen recently;
* resumable: interrupt it and the next run picks up from the cache.

Wikipedia gets a fast path through the official MediaWiki API, which answers for
up to 50 titles per request and reports missing pages and redirects explicitly.
That is both far more accurate than scraping status codes and far kinder to the
servers than 1.800 individual page loads.

**A URL that fails to answer never marks a fact as false.** It moves the item to
a state that blocks publication until a human looks at it — see
:mod:`src.content.editorial`.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

USER_AGENT = ("InstagramContentEngine-SourceAudit/1.0 "
              "(+https://github.com/AndreaBedei1/PagesInstagram; dataset source check)")

#: Hosts the datasets are allowed to cite. Anything else is reported, not fetched.
ALLOWED_HOSTS = frozenset({
    "it.wikipedia.org", "en.wikipedia.org", "www.treccani.it", "treccani.it",
})

#: Outcome of a single URL check.
OK = "ok"                     # 200, no redirect
REDIRECT = "redirect"         # 200 after one or more redirects
NOT_FOUND = "not_found"       # 404 / missing page
FORBIDDEN = "forbidden"       # 403
SOFT_404 = "soft_404"         # 200 but the page says it does not exist
SERVER_ERROR = "server_error"  # 5xx
TIMEOUT = "timeout"
ERROR = "error"               # connection / unexpected
BAD_SCHEME = "bad_scheme"
DISALLOWED_HOST = "disallowed_host"

#: Outcomes that mean "the cited page really is there".
REACHABLE = frozenset({OK, REDIRECT})
#: Outcomes that are the dataset's fault and will not fix themselves.
BROKEN = frozenset({NOT_FOUND, SOFT_404, BAD_SCHEME, DISALLOWED_HOST})
#: Outcomes that may be transient — never a reason to call a fact false.
TRANSIENT = frozenset({TIMEOUT, SERVER_ERROR, ERROR, FORBIDDEN})


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class UrlCheck:
    url: str
    status: str = ""
    http_status: int | None = None
    final_url: str = ""
    redirected: bool = False
    title: str = ""
    note: str = ""
    checked_at: str = ""
    elapsed_ms: int = 0

    @property
    def reachable(self) -> bool:
        return self.status in REACHABLE


class _HostRateLimiter:
    """Token bucket per host: never more than ``rate`` requests per second."""

    def __init__(self, rate: float = 4.0):
        self._min_interval = 1.0 / max(rate, 0.1)
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            earliest = self._next.get(host, 0.0)
            delay = max(0.0, earliest - now)
            self._next[host] = max(now, earliest) + self._min_interval
        if delay:
            time.sleep(delay)


class AuditCache:
    """URL → :class:`UrlCheck`, persisted as JSON so an audit can be resumed."""

    def __init__(self, path: str | Path, max_age_days: int = 30):
        self.path = Path(path)
        self.max_age_days = max_age_days
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._dirty = False
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def get(self, url: str) -> UrlCheck | None:
        raw = self._data.get(url)
        if not raw:
            return None
        checked = raw.get("checked_at") or ""
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.strptime(checked, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc)).days
        except ValueError:
            return None
        if age > self.max_age_days:
            return None
        return UrlCheck(**{k: v for k, v in raw.items()
                           if k in UrlCheck.__dataclass_fields__})

    def put(self, check: UrlCheck) -> None:
        with self._lock:
            self._data[check.url] = asdict(check)
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        self._dirty = False

    def __len__(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# Wikipedia fast path
# ---------------------------------------------------------------------------
_WIKI_API = "https://{host}/w/api.php"
_WIKI_BATCH = 50


def _wikipedia_title(url: str) -> str | None:
    p = urlparse(url)
    if not p.netloc.endswith("wikipedia.org") or not p.path.startswith("/wiki/"):
        return None
    return unquote(p.path[len("/wiki/"):]).replace("_", " ") or None


def check_wikipedia_batch(urls: list[str], *, session: requests.Session,
                          timeout: float = 20.0,
                          limiter: _HostRateLimiter | None = None) -> dict[str, UrlCheck]:
    """Resolve a batch of Wikipedia article URLs through the official API.

    The API reports missing pages (``missing``), normalisations and redirects
    explicitly, so this is more reliable than reading status codes — and one
    request covers up to 50 articles.
    """
    out: dict[str, UrlCheck] = {}
    by_title: dict[str, list[str]] = {}
    host = ""
    for u in urls:
        title = _wikipedia_title(u)
        if title is None:
            continue
        host = urlparse(u).netloc
        by_title.setdefault(title, []).append(u)
    if not by_title:
        return out

    titles = list(by_title)
    for start in range(0, len(titles), _WIKI_BATCH):
        chunk = titles[start:start + _WIKI_BATCH]
        if limiter:
            limiter.wait(host)
        t0 = time.monotonic()
        try:
            r = session.get(
                _WIKI_API.format(host=host),
                params={"action": "query", "format": "json", "redirects": "1",
                        "titles": "|".join(chunk)},
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            for title in chunk:
                for u in by_title[title]:
                    out[u] = UrlCheck(url=u, status=ERROR, note=f"API: {e}",
                                      checked_at=now_iso())
            continue
        elapsed = int((time.monotonic() - t0) * 1000 / max(len(chunk), 1))

        query = data.get("query") or {}
        # normalized/redirects map the requested title to the resolved one
        resolved: dict[str, str] = {}
        for entry in query.get("normalized") or []:
            resolved[entry["from"]] = entry["to"]
        redirect_targets: dict[str, str] = {}
        for entry in query.get("redirects") or []:
            redirect_targets[entry["from"]] = entry["to"]

        pages = (query.get("pages") or {}).values()
        by_final: dict[str, dict] = {p.get("title", ""): p for p in pages}

        for title in chunk:
            final = resolved.get(title, title)
            was_redirect = final in redirect_targets
            final = redirect_targets.get(final, final)
            page = by_final.get(final)
            for u in by_title[title]:
                if page is None:
                    out[u] = UrlCheck(url=u, status=NOT_FOUND, http_status=404,
                                      note="titolo non risolto dall'API",
                                      checked_at=now_iso(), elapsed_ms=elapsed)
                elif "missing" in page:
                    out[u] = UrlCheck(url=u, status=NOT_FOUND, http_status=404,
                                      note="la voce non esiste",
                                      checked_at=now_iso(), elapsed_ms=elapsed)
                else:
                    changed = was_redirect or final != title
                    out[u] = UrlCheck(
                        url=u, status=REDIRECT if changed else OK,
                        http_status=200, title=final,
                        final_url=f"https://{host}/wiki/{final.replace(' ', '_')}",
                        redirected=changed,
                        note="redirect" if changed else "",
                        checked_at=now_iso(), elapsed_ms=elapsed)
    return out


# ---------------------------------------------------------------------------
# Generic HTTP check
# ---------------------------------------------------------------------------
_SOFT_404_MARKERS = (
    "non esiste una voce",
    "wikipedia non ha ancora una voce",
    "la pagina che stai cercando non",
    "pagina non trovata",
)


def _redirects_to_root(url: str, final_url: str) -> bool:
    """Did the server answer 200 by throwing the request away?

    Treccani does exactly this: a ``/vocabolario/<lemma>/`` URL for a lemma it
    does not have redirects to ``https://www.treccani.it/`` with a 200. Scored
    naively that reads as "reachable, just moved", and 75 invented lemma links
    passed the first audit that way. A redirect that lands on the bare host when
    the request had a real path is a soft 404, not a move.
    """
    src, dst = urlparse(url), urlparse(final_url)
    if src.netloc != dst.netloc:
        return False
    src_path = src.path.strip("/")
    dst_path = dst.path.strip("/")
    return bool(src_path) and not dst_path and not dst.query


def check_url(url: str, *, session: requests.Session, timeout: float = 20.0,
              retries: int = 2, limiter: _HostRateLimiter | None = None) -> UrlCheck:
    """One URL, one verdict. Never raises; every failure becomes a status."""
    p = urlparse(url)
    if p.scheme != "https":
        return UrlCheck(url=url, status=BAD_SCHEME, checked_at=now_iso(),
                        note=f"schema {p.scheme!r} (atteso https)")
    if p.netloc not in ALLOWED_HOSTS:
        return UrlCheck(url=url, status=DISALLOWED_HOST, checked_at=now_iso(),
                        note=f"host non consentito: {p.netloc}")

    last_note = ""
    for attempt in range(retries + 1):
        if limiter:
            limiter.wait(p.netloc)
        t0 = time.monotonic()
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": USER_AGENT})
        except requests.Timeout:
            last_note = "timeout"
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return UrlCheck(url=url, status=TIMEOUT, note=last_note,
                            checked_at=now_iso())
        except requests.RequestException as e:
            last_note = str(e)[:200]
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return UrlCheck(url=url, status=ERROR, note=last_note,
                            checked_at=now_iso())

        elapsed = int((time.monotonic() - t0) * 1000)
        final = r.url
        redirected = final.rstrip("/") != url.rstrip("/")

        if r.status_code == 404:
            return UrlCheck(url=url, status=NOT_FOUND, http_status=404,
                            final_url=final, redirected=redirected,
                            checked_at=now_iso(), elapsed_ms=elapsed)
        if r.status_code == 403:
            return UrlCheck(url=url, status=FORBIDDEN, http_status=403,
                            final_url=final, checked_at=now_iso(),
                            note="accesso negato (può essere anti-bot)",
                            elapsed_ms=elapsed)
        if r.status_code >= 500:
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            return UrlCheck(url=url, status=SERVER_ERROR, http_status=r.status_code,
                            final_url=final, checked_at=now_iso(), elapsed_ms=elapsed)
        if r.status_code >= 400:
            return UrlCheck(url=url, status=ERROR, http_status=r.status_code,
                            final_url=final, checked_at=now_iso(), elapsed_ms=elapsed)

        if _redirects_to_root(url, final):
            return UrlCheck(url=url, status=SOFT_404, http_status=r.status_code,
                            final_url=final, redirected=True,
                            checked_at=now_iso(), elapsed_ms=elapsed,
                            note="reindirizzato alla home: la pagina non esiste")

        body = (r.text or "")[:4000].lower()
        if any(marker in body for marker in _SOFT_404_MARKERS):
            return UrlCheck(url=url, status=SOFT_404, http_status=r.status_code,
                            final_url=final, checked_at=now_iso(),
                            note="la pagina risponde 200 ma dichiara di non esistere",
                            elapsed_ms=elapsed)

        return UrlCheck(url=url, status=REDIRECT if redirected else OK,
                        http_status=r.status_code, final_url=final,
                        redirected=redirected, checked_at=now_iso(),
                        elapsed_ms=elapsed)

    return UrlCheck(url=url, status=ERROR, note=last_note, checked_at=now_iso())


# ---------------------------------------------------------------------------
# Dataset-level audit
# ---------------------------------------------------------------------------
@dataclass
class DatasetSourceAudit:
    dataset: str
    content_type: str = ""
    items: int = 0
    urls_total: int = 0
    urls_unique: int = 0
    checked: int = 0
    from_cache: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    duplicates: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    redirects: list[dict] = field(default_factory=list)
    hosts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def collect_urls(dataset_path: str | Path) -> tuple[dict, list[tuple[int, str]]]:
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    pairs = [(i, (item.get("source_url") or "").strip())
             for i, item in enumerate(data.get("items") or [])]
    return data, [(i, u) for i, u in pairs if u]


def audit_dataset(dataset_path: str | Path, *, cache: AuditCache,
                  session: requests.Session, limiter: _HostRateLimiter,
                  timeout: float = 20.0, retries: int = 2,
                  limit: int | None = None,
                  progress=None) -> tuple[DatasetSourceAudit, dict[str, UrlCheck]]:
    """Check every distinct source URL of one dataset.

    ``limit`` caps how many *uncached* URLs are fetched, so a partial audit can
    be run against a time budget and resumed later — the cache keeps what was
    already learnt.
    """
    path = Path(dataset_path)
    data, pairs = collect_urls(path)
    rep = DatasetSourceAudit(dataset=path.name,
                             content_type=data.get("content_type") or "",
                             items=len(data.get("items") or []),
                             urls_total=len(pairs))

    unique = sorted({u for _, u in pairs})
    rep.urls_unique = len(unique)
    rep.hosts = dict(Counter(urlparse(u).netloc for u in unique).most_common())

    seen: dict[str, list[int]] = {}
    for i, u in pairs:
        seen.setdefault(u, []).append(i)
    rep.duplicates = [{"url": u, "items": idx} for u, idx in seen.items()
                      if len(idx) > 1]

    checks: dict[str, UrlCheck] = {}
    pending: list[str] = []
    for u in unique:
        cached = cache.get(u)
        if cached is not None:
            checks[u] = cached
            rep.from_cache += 1
        else:
            pending.append(u)

    if limit is not None:
        pending = pending[:limit]

    wiki = [u for u in pending if _wikipedia_title(u)]
    other = [u for u in pending if not _wikipedia_title(u)]

    if wiki:
        for u, chk in check_wikipedia_batch(wiki, session=session, timeout=timeout,
                                            limiter=limiter).items():
            checks[u] = chk
            cache.put(chk)
            rep.checked += 1
            if progress:
                progress(chk)
        cache.save()

    for n, u in enumerate(other, 1):
        chk = check_url(u, session=session, timeout=timeout, retries=retries,
                        limiter=limiter)
        checks[u] = chk
        cache.put(chk)
        rep.checked += 1
        if progress:
            progress(chk)
        if n % 50 == 0:
            cache.save()
    cache.save()

    counts: Counter = Counter()
    for u in unique:
        chk = checks.get(u)
        counts[chk.status if chk else "not_checked"] += 1
        if chk is None:
            continue
        if chk.status in BROKEN or chk.status in TRANSIENT:
            rep.problems.append({"url": u, "status": chk.status,
                                 "http_status": chk.http_status,
                                 "note": chk.note, "items": seen.get(u, [])[:5]})
        elif chk.redirected:
            rep.redirects.append({"url": u, "final_url": chk.final_url,
                                  "title": chk.title, "items": seen.get(u, [])[:5]})
    rep.by_status = dict(counts.most_common())
    return rep, checks


def write_json_report(reports: list[DatasetSourceAudit], out_path: str | Path,
                      *, partial: bool) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now_iso(),
        "partial": partial,
        "user_agent": USER_AGENT,
        "totals": {
            "datasets": len(reports),
            "urls_total": sum(r.urls_total for r in reports),
            "urls_unique": sum(r.urls_unique for r in reports),
            "checked_now": sum(r.checked for r in reports),
            "from_cache": sum(r.from_cache for r in reports),
            "reachable": sum(v for r in reports for k, v in r.by_status.items()
                             if k in REACHABLE),
            "redirects": sum(len(r.redirects) for r in reports),
            "broken": sum(v for r in reports for k, v in r.by_status.items()
                          if k in BROKEN),
            "transient": sum(v for r in reports for k, v in r.by_status.items()
                             if k in TRANSIENT),
            "not_checked": sum(r.by_status.get("not_checked", 0) for r in reports),
        },
        "datasets": [r.as_dict() for r in reports],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out


_HTML_CSS = """
body{font:15px/1.55 system-ui,Segoe UI,sans-serif;margin:0;background:#f6f7f9;color:#1c1f23}
main{max-width:1100px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:34px 0 10px}
.sub{color:#5a6371;margin:0 0 26px}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 2px #0001;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #e6e9ee;vertical-align:top}
th{background:#eef1f5;font-weight:600}
td.url{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;word-break:break-all;max-width:460px}
.k{display:inline-block;padding:1px 8px;border-radius:11px;font-size:12px;font-weight:600}
.ok{background:#e3f5e8;color:#1c6b33}.warn{background:#fdf1d8;color:#8a5b00}
.bad{background:#fde5e5;color:#992222}.mut{background:#eceff3;color:#4a5361}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0 6px}
.card{background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 2px #0001;min-width:132px}
.card b{display:block;font-size:24px;line-height:1.2}.card span{color:#5a6371;font-size:13px}
.note{background:#fff8e6;border-left:4px solid #e0a800;padding:12px 16px;margin:20px 0;border-radius:0 8px 8px 0}
@media(prefers-color-scheme:dark){body{background:#15181c;color:#e6e9ee}table,.card{background:#1e2228;box-shadow:none}
th{background:#262b32}th,td{border-bottom:1px solid #2c323a}.note{background:#2a2418;color:#f0e2c0}}
"""


def _cls(status: str) -> str:
    if status in REACHABLE:
        return "ok"
    if status in BROKEN:
        return "bad"
    return "warn"


def write_html_report(reports: list[DatasetSourceAudit], out_path: str | Path,
                      *, partial: bool) -> Path:
    from html import escape
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    total_unique = sum(r.urls_unique for r in reports)
    reachable = sum(v for r in reports for k, v in r.by_status.items() if k in REACHABLE)
    broken = sum(v for r in reports for k, v in r.by_status.items() if k in BROKEN)
    transient = sum(v for r in reports for k, v in r.by_status.items() if k in TRANSIENT)
    unchecked = sum(r.by_status.get("not_checked", 0) for r in reports)
    redirects = sum(len(r.redirects) for r in reports)

    parts = [f"<!doctype html><meta charset='utf-8'><title>Audit delle fonti</title>"
             f"<style>{_HTML_CSS}</style><main>",
             "<h1>Audit di raggiungibilità delle fonti</h1>",
             f"<p class='sub'>Generato il {escape(now_iso())} · user agent "
             f"<code>{escape(USER_AGENT)}</code></p>"]

    if partial or unchecked:
        parts.append(
            "<div class='note'><b>Audit parziale.</b> Gli URL non ancora "
            "controllati restano nello stato <code>not_checked</code>: non sono "
            "dichiarati validi e non sbloccano la pubblicazione.</div>")

    parts.append("<div class='cards'>")
    for label, value in (("URL unici", total_unique), ("Raggiungibili", reachable),
                         ("Redirect", redirects), ("Rotti", broken),
                         ("Transitori", transient), ("Non controllati", unchecked)):
        parts.append(f"<div class='card'><b>{value}</b><span>{label}</span></div>")
    parts.append("</div>")

    parts.append("<h2>Per dataset</h2><table><tr><th>Dataset</th><th>Elementi</th>"
                 "<th>URL</th><th>Unici</th><th>Esiti</th><th>Host</th></tr>")
    for r in reports:
        badges = " ".join(
            f"<span class='k {_cls(k)}'>{escape(k)} {v}</span>"
            for k, v in r.by_status.items())
        hosts = ", ".join(f"{escape(h)} ({n})" for h, n in r.hosts.items())
        parts.append(f"<tr><td>{escape(r.dataset)}</td><td>{r.items}</td>"
                     f"<td>{r.urls_total}</td><td>{r.urls_unique}</td>"
                     f"<td>{badges}</td><td>{escape(hosts)}</td></tr>")
    parts.append("</table>")

    for r in reports:
        if not r.problems:
            continue
        parts.append(f"<h2>Problemi — {escape(r.dataset)} ({len(r.problems)})</h2>"
                     "<table><tr><th>Esito</th><th>URL</th><th>HTTP</th>"
                     "<th>Nota</th><th>Elementi</th></tr>")
        for p in r.problems[:400]:
            parts.append(
                f"<tr><td><span class='k {_cls(p['status'])}'>"
                f"{escape(p['status'])}</span></td>"
                f"<td class='url'>{escape(p['url'])}</td>"
                f"<td>{p.get('http_status') or '—'}</td>"
                f"<td>{escape(p.get('note') or '')}</td>"
                f"<td>{escape(str(p.get('items') or []))}</td></tr>")
        parts.append("</table>")
        if len(r.problems) > 400:
            parts.append(f"<p class='sub'>… e altri {len(r.problems) - 400}. "
                         f"L'elenco completo è nel report JSON.</p>")

    for r in reports:
        if not r.redirects:
            continue
        parts.append(f"<h2>Redirect — {escape(r.dataset)} ({len(r.redirects)})</h2>"
                     "<p class='sub'>Il contenuto è raggiungibile, ma l'URL citato "
                     "non è quello finale: vale la pena aggiornarlo.</p>"
                     "<table><tr><th>URL citato</th><th>Destinazione</th></tr>")
        for d in r.redirects[:200]:
            parts.append(f"<tr><td class='url'>{escape(d['url'])}</td>"
                         f"<td class='url'>{escape(d.get('final_url') or '')}</td></tr>")
        parts.append("</table>")
        if len(r.redirects) > 200:
            parts.append(f"<p class='sub'>… e altri {len(r.redirects) - 200}.</p>")

    parts.append("</main>")
    out.write_text("".join(parts), encoding="utf-8")
    return out


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT,
                      "Accept-Language": "it,en;q=0.7"})
    return s


def make_limiter(rate: float = 4.0) -> _HostRateLimiter:
    return _HostRateLimiter(rate)
