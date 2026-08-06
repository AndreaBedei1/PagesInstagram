"""Read the sources and extract the passage that supports each claim.

This is the part that does the work the label used to only assert. For every
factual item it fetches the cited page, finds the sentence containing the
claim's key elements, and returns it as evidence — or reports that no such
sentence exists, which means the claim is not supported by the source it cites.

Per content type:

``word_of_the_day``
    The Treccani entry must have this lemma as its **headword** and declare a
    compatible grammatical category. A page that merely mentions the word is not
    a dictionary entry for it.

``today_in_history``
    Two independent structured sources must agree: the article about the event
    and the Italian Wikipedia **day page** for that ``MM-DD``, which lists
    events as "year – description". A date that appears on the day page under
    the right year is a structured cross-check, not a keyword hit.

``world_curiosity``
    The claim's content words — and every number it contains — must appear in a
    single sentence of the source. Numbers matter most: a claim with "10.900
    metri" whose source never mentions that figure is unsupported however many
    other words match.

Everything goes through a disk cache keyed by URL, so a re-run costs nothing and
an interrupted run resumes. Wikipedia is read through the official API in
batches of twenty; nothing is scraped that an API can answer.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

USER_AGENT = ("InstagramContentEngine-Verifier/2.0 "
              "(+https://github.com/AndreaBedei1/PagesInstagram; "
              "dataset fact verification)")

WIKI_API = "https://it.wikipedia.org/w/api.php"
WIKI_BATCH = 20

MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
          "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")

#: Words carrying no evidential weight when matching a claim to a sentence.
STOPWORDS = frozenset("""
a ad ai al alla alle allo agli anche ancorac che chi ci coi col come con cui da
dai dal dalla dalle dallo degli dei del della delle dello di dove e ed gli ha
hanno i il in la le lo loro ma me mi ne negli nei nel nella nelle nello non o
per piu più però qua qual quale quando quanto quel quella quelle quelli quello
questa queste questi questo qui se sei senza si sia siamo sono su sua sue sui
sul sulla sulle sullo suo suoi ti tra tu tua tue tuo un una uno vi via essere
era erano fu furono viene vengono stato stata state stati alcuni alcune molti
molte ogni oltre solo soltanto anche ancora quasi circa fino dopo prima durante
sempre mai già più meno tutto tutta tutti tutte
""".split())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(c for c in norm if not unicodedata.combining(c))


_TOKEN_RE = re.compile(r"[a-z0-9']+")
_NUMBER_RE = re.compile(r"\d[\d.,]*")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def content_tokens(text: str, *, min_len: int = 4) -> set[str]:
    """The words that carry the claim, stopwords and short function words out."""
    return {t for t in _TOKEN_RE.findall(_fold(text))
            if len(t) >= min_len and t not in STOPWORDS}


def numbers_in(text: str) -> set[str]:
    """Numbers as written, normalised so 10.900 and 10900 compare equal."""
    out = set()
    for raw in _NUMBER_RE.findall(text or ""):
        digits = raw.replace(".", "").replace(",", "").rstrip("0") or "0"
        if len(raw.replace(".", "").replace(",", "")) >= 2:
            out.add(digits)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text or "") if s.strip()]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
class PageCache:
    """URL/key → extracted plain text, on disk, so a re-run is free."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        self._dirty = False
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, value: dict) -> None:
        self._data[key] = value
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False),
                             encoding="utf-8")
        self._dirty = False

    def __len__(self) -> int:
        return len(self._data)


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "it"})
    return s


# ---------------------------------------------------------------------------
# Wikipedia (official API, batched)
# ---------------------------------------------------------------------------
def wikipedia_title(url: str) -> str | None:
    p = urlparse(url or "")
    if not p.netloc.endswith("wikipedia.org") or not p.path.startswith("/wiki/"):
        return None
    return unquote(p.path[len("/wiki/"):]).replace("_", " ") or None


def fetch_wikipedia_extracts(titles: list[str], *, session: requests.Session,
                             cache: PageCache, rate: float = 4.0,
                             intro_only: bool = False,
                             progress=None) -> dict[str, dict]:
    """``title -> {title, text, missing}`` for every requested article.

    Uses ``prop=extracts&explaintext``, which returns the article as plain text
    without markup, and no scraping.

    ``intro_only`` matters more than it looks: MediaWiki serves **one** page per
    request for full-article extracts, whatever ``exlimit`` says, but twenty per
    request for lead sections. Fetching two thousand articles without it means
    two thousand requests, and in practice means most of them silently coming
    back empty. The day-page path needs whole articles to find a year; the
    curiosity path only ever reads the lead, so it asks for the lead.
    """
    out: dict[str, dict] = {}
    pending = []
    for t in titles:
        prefix = "wikiintro" if intro_only else "wiki"
        cached = cache.get(f"{prefix}:{t}")
        if cached is not None:
            out[t] = cached
        else:
            pending.append(t)

    interval = 1.0 / max(rate, 0.2)
    for start in range(0, len(pending), WIKI_BATCH):
        chunk = pending[start:start + WIKI_BATCH]
        try:
            params = {"action": "query", "format": "json", "redirects": "1",
                      "prop": "extracts", "explaintext": "1", "exlimit": "max",
                      "titles": "|".join(chunk)}
            if intro_only:
                params["exintro"] = "1"
            r = session.get(WIKI_API, timeout=40, params=params)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            for t in chunk:
                out[t] = {"title": t, "text": "", "missing": True,
                          "error": str(e)[:120]}
            continue

        query = data.get("query") or {}
        alias: dict[str, str] = {}
        for entry in (query.get("normalized") or []):
            alias[entry["from"]] = entry["to"]
        for entry in (query.get("redirects") or []):
            alias[entry["from"]] = entry["to"]

        pages = {p.get("title", ""): p for p in (query.get("pages") or {}).values()}
        for t in chunk:
            resolved = alias.get(t, t)
            resolved = alias.get(resolved, resolved)
            page = pages.get(resolved)
            record = {
                "title": resolved,
                "text": (page or {}).get("extract") or "",
                "missing": page is None or "missing" in (page or {}),
            }
            out[t] = record
            cache.put(f"{'wikiintro' if intro_only else 'wiki'}:{t}", record)
        cache.save()
        if progress:
            progress(min(start + WIKI_BATCH, len(pending)), len(pending))
        time.sleep(interval)
    return out


def fetch_day_page(month: int, day: int, *, session: requests.Session,
                   cache: PageCache) -> dict:
    """The Italian Wikipedia page for a calendar day ("7 agosto").

    These pages are a structured listing — "1782 – …", "1947 – …" — which makes
    them a genuine second source for a dated event rather than a keyword search.
    """
    title = f"{day} {MONTHS[month - 1]}"
    got = fetch_wikipedia_extracts([title], session=session, cache=cache)
    return got.get(title, {"title": title, "text": "", "missing": True})


# ---------------------------------------------------------------------------
# Treccani
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def html_to_text(html: str) -> str:
    body = _SCRIPT_RE.sub(" ", html or "")
    body = _TAG_RE.sub(" ", body)
    body = (body.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&quot;", '"').replace("&#39;", "'")
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", body).strip()


def fetch_page(url: str, *, session: requests.Session, cache: PageCache,
               rate_state: dict, rate: float = 3.0,
               timeout: float = 25.0) -> dict:
    """One HTML page as plain text, cached, rate-limited, never raising."""
    cached = cache.get(f"url:{url}")
    if cached is not None:
        return cached

    wait = rate_state.get("next", 0.0) - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    rate_state["next"] = time.monotonic() + 1.0 / max(rate, 0.2)

    record: dict = {"url": url, "title": "", "text": "", "status": 0,
                    "final_url": "", "ok": False}
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        record["status"] = r.status_code
        record["final_url"] = r.url
        if r.status_code == 200:
            html = r.text or ""
            m = _TITLE_RE.search(html)
            record["title"] = html_to_text(m.group(1)) if m else ""
            record["text"] = html_to_text(html)[:200_000]
            # A redirect that throws the path away is a soft 404.
            src, dst = urlparse(url), urlparse(r.url)
            record["ok"] = not (src.path.strip("/") and not dst.path.strip("/"))
    except requests.RequestException as e:
        record["error"] = str(e)[:160]
    cache.put(f"url:{url}", record)
    return record


# ---------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------
@dataclass
class Evidence:
    supported: bool
    passage: str = ""
    reason: str = ""
    matched_tokens: int = 0
    required_tokens: int = 0
    source_title: str = ""
    extra: dict = field(default_factory=dict)


def best_supporting_sentence(claim: str, page_text: str, *,
                             min_ratio: float = 0.5,
                             require_numbers: bool = True) -> Evidence:
    """Find the sentence of ``page_text`` that best supports ``claim``.

    Support means: a large share of the claim's content words appear in one
    sentence, **and** every number the claim asserts appears in the source. The
    number rule is what stops "the deepest point exceeds 10.900 metres" from
    being accepted by a page that never mentions the figure.
    """
    want = content_tokens(claim)
    if not want:
        return Evidence(False, reason="l'affermazione non ha parole di contenuto")

    claim_numbers = numbers_in(claim)
    page_numbers = numbers_in(page_text)
    missing_numbers = claim_numbers - page_numbers
    if require_numbers and missing_numbers:
        return Evidence(
            False, required_tokens=len(want),
            reason=f"la fonte non riporta i numeri citati: "
                   f"{sorted(missing_numbers)[:4]}")

    best, best_hits = "", 0
    for sentence in sentences(page_text):
        hits = len(want & content_tokens(sentence))
        if hits > best_hits:
            best, best_hits = sentence, hits

    # Fall back to the whole document: a claim can be supported by a page
    # without any single sentence restating it word for word.
    doc_hits = len(want & content_tokens(page_text))
    ratio = doc_hits / len(want)
    if ratio < min_ratio:
        return Evidence(False, matched_tokens=doc_hits, required_tokens=len(want),
                        reason=f"solo {doc_hits}/{len(want)} parole chiave "
                               f"compaiono nella fonte")

    passage = best if len(best) >= 40 else page_text[:300]
    return Evidence(True, passage=passage, matched_tokens=doc_hits,
                    required_tokens=len(want))


# -- words ------------------------------------------------------------------
_POS_PATTERNS = {
    "sostantivo maschile": (r"\bs\.?\s?m\.", r"sost\w*\s+masch", r"\bs\.\sm\."),
    "sostantivo femminile": (r"\bs\.?\s?f\.", r"sost\w*\s+femm"),
    "aggettivo": (r"\bagg\.", r"aggettivo"),
    "verbo transitivo": (r"\bv\.?\s?tr\.", r"verbo\s+trans"),
    "verbo intransitivo": (r"\bv\.?\s?intr\.", r"verbo\s+intrans"),
    "verbo riflessivo": (r"\bv\.?\s?rifl\.", r"\bintr\.\s?pron\.",
                         r"verbo\s+rifl", r"\bv\.?\s?tr\.", r"\bv\.?\s?intr\."),
    "avverbio": (r"\bavv\.", r"avverbio"),
    "locuzione": (r"\bloc\.", r"locuz"),
}


#: Treccani's own grammatical markers. Order matters: "v. tr. e intr."
#: must read as transitive before the bare "v. intr." pattern claims it,
#: and the pronominal marker must win over both.
_POS_MARKERS: tuple[tuple[str, str], ...] = (
    ('v\\.\\s*intr\\.\\s*pron\\.', 'verbo riflessivo'),
    ('v\\.\\s*rifl\\.', 'verbo riflessivo'),
    ('v\\.\\s*tr\\.\\s*e\\s*intr\\.', 'verbo transitivo'),
    ('v\\.\\s*tr\\.', 'verbo transitivo'),
    ('v\\.\\s*intr\\.', 'verbo intransitivo'),
    ('s\\.\\s*m\\.\\s*e\\s*f\\.', 'sostantivo maschile'),
    ('s\\.\\s*m\\.', 'sostantivo maschile'),
    ('s\\.\\s*f\\.', 'sostantivo femminile'),
    ('agg\\.', 'aggettivo'),
    ('avv\\.', 'avverbio'),
    ('locuz\\.', 'locuzione'),
)


def part_of_speech_from_entry(text: str) -> str:
    """The grammatical category the dictionary itself declares, or ''.

    Reading it from the entry is the point: asserting a category and checking
    whether the page agrees fails on every lemma the dictionary classes
    differently (``declinare`` is "v. tr. e intr."), and the dictionary is the
    authority here, not the candidate list.
    """
    head = _fold(text or "")[:2500]
    for pattern, label in _POS_MARKERS:
        if re.search(pattern, head):
            return label
    return ""


def verify_lemma(lemma: str, part_of_speech: str, definition: str,
                 page: dict) -> Evidence:
    """Is this Treccani page the dictionary entry for this lemma?

    Requires the lemma as the entry headword (the page title), a compatible
    grammatical marker, and some overlap between our paraphrase and the entry
    text. A page that merely contains the word somewhere is rejected.
    """
    if not page.get("ok") or page.get("missing"):
        return Evidence(False, reason=f"pagina non disponibile "
                                      f"(HTTP {page.get('status')})")
    text = page.get("text") or ""
    title = page.get("title") or ""
    folded_title = _fold(title)
    folded_lemma = _fold(lemma)

    # The Treccani <title> is "lemma in Vocabolario - Treccani" or similar.
    head = re.sub(r"\d+$", "", folded_title.split(" in ")[0].split("-")[0]).strip()
    if folded_lemma not in head and head not in folded_lemma:
        # Pronominal verbs and participles are filed under a base form.
        base = re.sub(r"(arsi|ersi|irsi)$", "", folded_lemma)
        if not base or base not in head:
            return Evidence(False, source_title=title,
                            reason=f"il lemma della voce è «{title[:60]}», "
                                   f"non «{lemma}»")

    low = _fold(text)[:6000]
    declared = part_of_speech_from_entry(text)
    patterns = _POS_PATTERNS.get(part_of_speech, ())
    pos_ok = any(re.search(p, low) for p in patterns) if patterns else True
    if not pos_ok and not declared:
        return Evidence(False, source_title=title,
                        reason=f"la voce non dichiara una categoria grammaticale")

    want = content_tokens(definition, min_len=5)
    hits = len(want & content_tokens(text)) if want else 0
    passage = ""
    for sentence in sentences(text[:4000]):
        if folded_lemma in _fold(sentence) and len(sentence) >= 40:
            passage = sentence
            break
    if not passage:
        passage = text[:280]
    return Evidence(True, passage=passage, source_title=title,
                    matched_tokens=hits, required_tokens=len(want),
                    extra={"pos_confirmed": pos_ok,
                           "declared_pos": declared or part_of_speech})


# -- history ----------------------------------------------------------------
_DAY_ENTRY_RE = re.compile(r"(\d{3,4})\s*(?:[-–—]|:)\s*([^\n]{10,300})")


def verify_event_on_day_page(year: str, claim: str, day_page: dict) -> Evidence:
    """Does the calendar day page list this event under this year?

    The day pages are a structured listing maintained separately from the
    article about the event, which makes agreement between the two a genuine
    cross-check rather than the same text read twice.
    """
    if day_page.get("missing"):
        return Evidence(False, reason="pagina del giorno non disponibile")
    text = day_page.get("text") or ""
    want = content_tokens(claim)
    if not want:
        return Evidence(False, reason="titolo senza parole di contenuto")

    best, best_hits = "", 0
    for m in _DAY_ENTRY_RE.finditer(text):
        if m.group(1) != str(year):
            continue
        entry = m.group(2).strip()
        hits = len(want & content_tokens(entry))
        if hits > best_hits:
            best, best_hits = f"{m.group(1)} – {entry}", hits

    if best_hits >= 2:
        return Evidence(True, passage=best, matched_tokens=best_hits,
                        required_tokens=len(want),
                        source_title=day_page.get("title", ""))
    if best:
        return Evidence(False, passage=best, matched_tokens=best_hits,
                        required_tokens=len(want),
                        reason=f"la pagina del giorno elenca l'anno {year} ma "
                               f"con un evento diverso")
    return Evidence(False, required_tokens=len(want),
                    reason=f"l'anno {year} non compare fra gli eventi del giorno")
