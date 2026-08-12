"""Run the evidence extraction over the whole corpus and record what it found.

One pass per dataset. Every factual item either comes out with a passage from
its source attached and a hash bound to its text, or with the reason no such
passage exists — which is the signal to fix the claim or replace it.

Nothing here can approve an item without evidence: the only writer is
:func:`src.content.verification.attach_verification`, which raises if the
evidence is too short or the source is unclassified.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .dataset_io import load_dataset, save_dataset
from .evidence import (Evidence, PageCache, best_supporting_sentence,
                       build_session, fetch_day_page, fetch_page,
                       fetch_wikipedia_extracts, today_iso, verify_event_on_day_page,
                       verify_lemma, wikipedia_title)
from .verification import (METHOD_AUTHORITATIVE, METHOD_CROSS_CHECKED,
                           METHOD_STRUCTURED, attach_verification, claim_hash,
                           mark_original, strength_for_host)


@dataclass
class ItemOutcome:
    sequence_index: int
    text: str
    verified: bool
    method: str = ""
    reason: str = ""
    source_url: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DatasetOutcome:
    dataset: str
    content_type: str = ""
    total: int = 0
    verified: int = 0
    failed: int = 0
    by_method: dict = field(default_factory=dict)
    by_reason: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"dataset": self.dataset, "content_type": self.content_type,
                "total": self.total, "verified": self.verified,
                "failed": self.failed, "by_method": self.by_method,
                "by_reason": self.by_reason,
                "failures": [f.as_dict() for f in self.failures]}


def _reason_bucket(reason: str) -> str:
    """Group reasons so the report shows patterns, not a thousand strings."""
    for needle, label in (
            ("non riporta i numeri", "numeri assenti dalla fonte"),
            ("parole chiave", "sovrapposizione lessicale insufficiente"),
            ("pagina non disponibile", "pagina irraggiungibile"),
            ("il lemma della voce", "lemma diverso nella voce"),
            ("non dichiara", "categoria grammaticale non confermata"),
            ("non compare fra gli eventi", "anno assente dalla pagina del giorno"),
            ("evento diverso", "evento diverso nella pagina del giorno"),
            ("pagina del giorno non disponibile", "pagina del giorno assente"),
    ):
        if needle in reason:
            return label
    return reason[:60]


# ---------------------------------------------------------------------------
def verify_original(path: Path) -> DatasetOutcome:
    """Non-factual writing: bind each text to its hash, no source involved."""
    data = load_dataset(path)
    out = DatasetOutcome(dataset=path.name,
                         content_type=data.get("content_type") or "")
    items = data.get("items") or []
    out.total = len(items)
    for item in items:
        mark_original(item)
        out.verified += 1
    out.by_method = {"original_nonfactual": out.verified}
    save_dataset(path, data)
    return out


def verify_words(path: Path, *, cache: PageCache, session, rate: float = 3.0,
                 progress=None) -> DatasetOutcome:
    data = load_dataset(path)
    out = DatasetOutcome(dataset=path.name,
                         content_type=data.get("content_type") or "")
    items = data.get("items") or []
    out.total = len(items)
    methods: Counter = Counter()
    reasons: Counter = Counter()
    rate_state: dict = {}

    for n, item in enumerate(items, 1):
        url = item.get("source_url") or ""
        meta = item.get("metadata") or {}
        page = fetch_page(url, session=session, cache=cache,
                          rate_state=rate_state, rate=rate)
        ev = verify_lemma(item.get("text") or "",
                          str(meta.get("part_of_speech") or ""),
                          str(meta.get("definition") or ""), page)
        if ev.supported:
            attach_verification(
                item, method=METHOD_AUTHORITATIVE, evidence=ev.passage,
                source_url=page.get("final_url") or url,
                source_title=ev.source_title or page.get("title") or "",
                source_strength=strength_for_host(urlparse(url).netloc)
                or "authoritative_reference",
                checked_at=today_iso(),
                note="voce di vocabolario: lemma e categoria confermati")
            out.verified += 1
            methods[METHOD_AUTHORITATIVE] += 1
        else:
            out.failed += 1
            reasons[_reason_bucket(ev.reason)] += 1
            out.failures.append(ItemOutcome(
                sequence_index=item.get("sequence_index", -1),
                text=(item.get("text") or "")[:60], verified=False,
                reason=ev.reason, source_url=url))
        if progress and n % 100 == 0:
            progress(n, out.total)
        if n % 200 == 0:
            cache.save()

    cache.save()
    out.by_method, out.by_reason = dict(methods), dict(reasons.most_common())
    save_dataset(path, data)
    return out


def verify_history(path: Path, *, cache: PageCache, session, rate: float = 4.0,
                   progress=None) -> DatasetOutcome:
    data = load_dataset(path)
    out = DatasetOutcome(dataset=path.name,
                         content_type=data.get("content_type") or "")
    items = data.get("items") or []
    out.total = len(items)
    methods: Counter = Counter()
    reasons: Counter = Counter()

    # One batch fetch for every article, then one per calendar day.
    titles = [t for t in
              {wikipedia_title(i.get("source_url") or "") for i in items} if t]
    articles = fetch_wikipedia_extracts(titles, session=session, cache=cache,
                                        rate=rate, progress=progress)

    day_pages: dict[str, dict] = {}
    for item in items:
        key = str(item.get("calendar_key") or "")
        if len(key) == 5 and key not in day_pages:
            day_pages[key] = fetch_day_page(int(key[:2]), int(key[3:]),
                                            session=session, cache=cache)
    cache.save()

    for n, item in enumerate(items, 1):
        url = item.get("source_url") or ""
        title = wikipedia_title(url)
        article = articles.get(title or "", {})
        key = str(item.get("calendar_key") or "")
        year = str((item.get("metadata") or {}).get("year") or "")
        claim = item.get("text") or ""

        day_ev = verify_event_on_day_page(year, claim, day_pages.get(key, {}))
        art_ev: Evidence | None = None
        if article and not article.get("missing"):
            art_ev = best_supporting_sentence(claim, article.get("text") or "",
                                              min_ratio=0.4,
                                              require_numbers=False)

        # The evidence names where it came from. A day-page entry like
        # "1959 – Debutta la bambola Barbie" is a complete structured record but
        # a poor citation on its own: a reader has to be told it comes from the
        # listing for that calendar day, on which page, and whether the article
        # about the event agrees.
        day_title = (day_pages.get(key, {}) or {}).get("title") or key
        if day_ev.supported and art_ev and art_ev.supported:
            method = METHOD_CROSS_CHECKED
            evidence = (f"Elenco del {day_title} (Wikipedia in italiano): "
                        f"«{day_ev.passage}». Confermato dalla voce "
                        f"«{article.get('title') or title}».")
        elif day_ev.supported:
            method = METHOD_STRUCTURED
            evidence = (f"Elenco del {day_title} (Wikipedia in italiano): "
                        f"«{day_ev.passage}».")
        else:
            out.failed += 1
            reasons[_reason_bucket(day_ev.reason)] += 1
            out.failures.append(ItemOutcome(
                sequence_index=item.get("sequence_index", -1),
                text=claim[:60], verified=False,
                reason=day_ev.reason, source_url=url))
            continue

        attach_verification(
            item, method=method, evidence=evidence,
            source_url=url,
            source_title=article.get("title") or (title or ""),
            source_strength=strength_for_host(urlparse(url).netloc)
            or "general_encyclopedia",
            checked_at=today_iso(),
            note=f"evento elencato nella pagina del {key} sotto l'anno {year}")
        out.verified += 1
        methods[method] += 1
        if progress and n % 200 == 0:
            progress(n, out.total)

    out.by_method, out.by_reason = dict(methods), dict(reasons.most_common())
    save_dataset(path, data)
    return out


def verify_curiosities(path: Path, *, cache: PageCache, session,
                       rate: float = 4.0, progress=None) -> DatasetOutcome:
    data = load_dataset(path)
    out = DatasetOutcome(dataset=path.name,
                         content_type=data.get("content_type") or "")
    items = data.get("items") or []
    out.total = len(items)
    methods: Counter = Counter()
    reasons: Counter = Counter()

    titles = [t for t in
              {wikipedia_title(i.get("source_url") or "") for i in items} if t]
    articles = fetch_wikipedia_extracts(titles, session=session, cache=cache,
                                        rate=rate, progress=progress)
    rate_state: dict = {}

    for n, item in enumerate(items, 1):
        url = item.get("source_url") or ""
        claim = item.get("text") or ""
        title = wikipedia_title(url)
        if title:
            page = articles.get(title, {})
            text, page_title = page.get("text") or "", page.get("title") or title
            available = not page.get("missing")
        else:
            page = fetch_page(url, session=session, cache=cache,
                              rate_state=rate_state, rate=3.0)
            text, page_title = page.get("text") or "", page.get("title") or ""
            available = bool(page.get("ok"))

        if not available:
            out.failed += 1
            reasons["pagina irraggiungibile"] += 1
            out.failures.append(ItemOutcome(
                sequence_index=item.get("sequence_index", -1), text=claim[:60],
                verified=False, reason="fonte non disponibile", source_url=url))
            continue

        ev = best_supporting_sentence(claim, text, min_ratio=0.5,
                                      require_numbers=True)
        if not ev.supported:
            out.failed += 1
            reasons[_reason_bucket(ev.reason)] += 1
            out.failures.append(ItemOutcome(
                sequence_index=item.get("sequence_index", -1), text=claim[:60],
                verified=False, reason=ev.reason, source_url=url))
            continue

        strength = strength_for_host(urlparse(url).netloc)
        if strength is None:
            out.failed += 1
            reasons["host non classificato"] += 1
            out.failures.append(ItemOutcome(
                sequence_index=item.get("sequence_index", -1), text=claim[:60],
                verified=False, reason="host non classificato", source_url=url))
            continue

        method = (METHOD_AUTHORITATIVE if strength != "general_encyclopedia"
                  else METHOD_STRUCTURED)
        attach_verification(
            item, method=method, evidence=ev.passage, source_url=url,
            source_title=page_title, source_strength=strength,
            checked_at=today_iso(),
            note=f"{ev.matched_tokens}/{ev.required_tokens} parole chiave "
                 f"presenti nella fonte")
        out.verified += 1
        methods[method] += 1
        if progress and n % 200 == 0:
            progress(n, out.total)

    cache.save()
    out.by_method, out.by_reason = dict(methods), dict(reasons.most_common())
    save_dataset(path, data)
    return out


# ---------------------------------------------------------------------------
DISPATCH = {
    "philosophical_thought": "original",
    "daily_question": "original",
    "word_of_the_day": "words",
    "today_in_history": "history",
    "world_curiosity": "curiosities",
}


def cache_for(cache_dir: str | Path, dataset_name: str) -> PageCache:
    """One cache file per dataset.

    A single shared file is a last-writer-wins race the moment two datasets are
    verified concurrently — the second save overwrites the first dataset's
    entries and the work is silently lost. Per-dataset files make concurrent
    runs safe and keep an interrupted run resumable.
    """
    stem = Path(dataset_name).stem
    return PageCache(Path(cache_dir) / f"evidence_{stem}.json")


def verify_all(datasets_dir: str | Path, *, cache_dir: str | Path,
               rate: float = 4.0, progress=None) -> list[DatasetOutcome]:
    session = build_session()
    outcomes: list[DatasetOutcome] = []
    for path in sorted(Path(datasets_dir).glob("*.json")):
        ctype = (load_dataset(path).get("content_type") or "")
        kind = DISPATCH.get(ctype)
        cache = cache_for(cache_dir, path.name)
        if kind == "original":
            outcomes.append(verify_original(path))
        elif kind == "words":
            outcomes.append(verify_words(path, cache=cache, session=session,
                                         rate=3.0, progress=progress))
        elif kind == "history":
            outcomes.append(verify_history(path, cache=cache, session=session,
                                           rate=rate, progress=progress))
        elif kind == "curiosities":
            outcomes.append(verify_curiosities(path, cache=cache,
                                               session=session, rate=rate,
                                               progress=progress))
    cache.save()
    return outcomes


def write_report(outcomes: list[DatasetOutcome], out_path: str | Path) -> Path:
    from .evidence import now_iso

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now_iso(),
        "totals": {
            "items": sum(o.total for o in outcomes),
            "verified": sum(o.verified for o in outcomes),
            "failed": sum(o.failed for o in outcomes),
        },
        "datasets": [o.as_dict() for o in outcomes],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
