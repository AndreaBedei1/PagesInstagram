"""Repetition analysis and stratified sampling for editorial review.

Two questions this answers that no other check does:

1. **Does the account read as a script?** Not "is anything duplicated" — nothing
   is — but "how long before a reader sees the same closing line, the same
   hashtag set, the same background, the same category run". A dataset with
   1.000 unique texts still looks generated if it has six calls to action.

2. **What does a human actually have to read?** 5.000 items cannot be reviewed.
   :func:`stratified_sample` picks a fixed, reproducible slice that covers the
   beginning, middle and end of each dataset, every category and mood, the
   items containing numbers, the absolute claims, each source host and both
   ends of the length distribution — so the sample is not just "the first 100".

Both are pure functions of the datasets plus a seed. Same seed, same sample.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .normalize import normalize_text

#: Windows, in days, over which repetition is measured. A page publishes one
#: item per day in ``sequence_index`` order, so a window is a slice of the
#: sequence.
WINDOWS = (7, 30, 90, 365)

#: Thresholds. Exceeding one is a warning in the report and a test failure in
#: ``tests/unit/test_editorial_quality.py``.
MAX_SAME_CTA_IN_WINDOW = {7: 1, 30: 2, 90: 5, 365: 18}
MAX_SAME_HASHTAG_SET_IN_WINDOW = {7: 2, 30: 5, 90: 12, 365: 40}
MAX_SAME_PROMPT_IN_WINDOW = {7: 1, 30: 3, 90: 8, 365: 26}
MAX_SAME_CATEGORY_RUN = 4
MAX_SAME_MOOD_RUN = 6

#: Prefix compared when looking for captions that read the same. Reported, but
#: not a warning on its own: "La data è celebrata come giornata mondiale della
#: salute" and "…dell'ambiente" share 40 characters and are entirely fine. What
#: *is* a defect is two items whose caption body is identical once the "Fonte:"
#: sentence is removed — the same sentence explaining two different events.
CAPTION_PREFIX = 40
_SOURCE_SUFFIX = " fonte:"

_NUMBER_RE = re.compile(r"\d")
_ABSOLUTE_RE = re.compile(
    r"\b(unic\w+|il solo\b|primo\b|prima\b|mai\b|sempre\b|più grande|più antic\w+|"
    r"più profond\w+|più velo\w+|dallo spazio)\b", re.IGNORECASE)


@dataclass
class WindowFinding:
    window: int
    kind: str
    value: str
    count: int
    start_index: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DatasetStats:
    dataset: str
    content_type: str = ""
    items: int = 0
    distinct_cta: int = 0
    distinct_cta_normalised: int = 0
    empty_cta: int = 0
    distinct_captions: int = 0
    near_identical_captions: int = 0
    duplicate_caption_bodies: int = 0
    distinct_hashtag_sets: int = 0
    distinct_hashtags: int = 0
    distinct_prompts: int = 0
    distinct_categories: int = 0
    distinct_moods: int = 0
    top_cta: list = field(default_factory=list)
    top_hashtag_sets: list = field(default_factory=list)
    top_prompts: list = field(default_factory=list)
    length_stats: dict = field(default_factory=dict)
    longest_category_run: int = 0
    longest_mood_run: int = 0
    findings: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings

    def as_dict(self) -> dict:
        return asdict(self)


def publication_order(items: list[dict], content_type: str = "") -> list[dict]:
    """The order a reader actually sees, which is not always ``sequence_index``.

    Cyclic pages walk ``sequence_index``, one per day. ``today_in_history``
    walks the **calendar**: on each date it publishes an event whose
    ``calendar_key`` matches, picking among that day's events by year. Measuring
    its repetition in sequence order would answer a question nobody asks — the
    reader sees 01-01, 01-02, 01-03, so that is the order this returns, taking
    the first event of each day (the year-0 rotation).
    """
    if content_type == "today_in_history":
        by_key: dict[str, list[dict]] = {}
        for item in items:
            by_key.setdefault(str(item.get("calendar_key") or ""), []).append(item)
        out = []
        for key in sorted(by_key):
            same_day = sorted(by_key[key],
                              key=lambda i: (i.get("sequence_index") is None,
                                             i.get("sequence_index") or 0))
            out.append(same_day[0])
        return out
    return sorted(items, key=lambda i: (i.get("sequence_index") is None,
                                        i.get("sequence_index") or 0))


def _by_sequence(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: (i.get("sequence_index") is None,
                                        i.get("sequence_index") or 0))


def _longest_run(values: list) -> int:
    best = run = 0
    prev = object()
    for v in values:
        run = run + 1 if v == prev else 1
        prev = v
        best = max(best, run)
    return best


def _window_findings(ordered: list[dict]) -> list[WindowFinding]:
    """Repetitions inside every sliding window, reported once per (window, value)."""
    out: list[WindowFinding] = []
    seen: set[tuple] = set()

    def scan(kind: str, key, limits: dict[int, int]) -> None:
        values = [key(i) for i in ordered]
        for window, limit in limits.items():
            if window > len(values):
                continue
            for start in range(0, len(values) - window + 1):
                counts = Counter(v for v in values[start:start + window] if v)
                for value, n in counts.items():
                    if n > limit and (kind, window, value) not in seen:
                        seen.add((kind, window, value))
                        out.append(WindowFinding(window=window, kind=kind,
                                                 value=str(value)[:120], count=n,
                                                 start_index=start))
    scan("call_to_action", lambda i: (i.get("call_to_action") or "").strip(),
         MAX_SAME_CTA_IN_WINDOW)
    scan("hashtag_set", lambda i: " ".join(sorted(i.get("hashtags") or [])),
         MAX_SAME_HASHTAG_SET_IN_WINDOW)
    scan("background_prompt", lambda i: (i.get("background_prompt") or "").strip(),
         MAX_SAME_PROMPT_IN_WINDOW)
    return out


def analyse_dataset(path: str | Path) -> DatasetStats:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("items") or []
    st = DatasetStats(dataset=Path(path).name,
                      content_type=data.get("content_type") or "",
                      items=len(items))

    ctas = [(i.get("call_to_action") or "").strip() for i in items]
    st.empty_cta = sum(1 for c in ctas if not c)
    st.distinct_cta = len({c for c in ctas if c})
    st.distinct_cta_normalised = len({normalize_text(c) for c in ctas if c})
    st.top_cta = Counter(c for c in ctas if c).most_common(8)

    captions = [(i.get("caption") or "").strip() for i in items]
    st.distinct_captions = len(set(captions))
    prefixes = Counter(normalize_text(c)[:CAPTION_PREFIX] for c in captions if c)
    st.near_identical_captions = sum(n for n in prefixes.values() if n > 1)
    bodies = Counter(normalize_text(c).split(_SOURCE_SUFFIX)[0].strip()
                     for c in captions if c)
    st.duplicate_caption_bodies = sum(n for n in bodies.values() if n > 1)

    sets = [" ".join(sorted(i.get("hashtags") or [])) for i in items]
    st.distinct_hashtag_sets = len(set(sets))
    st.top_hashtag_sets = Counter(sets).most_common(5)
    st.distinct_hashtags = len({t for i in items for t in (i.get("hashtags") or [])})

    prompts = [(i.get("background_prompt") or "").strip() for i in items]
    st.distinct_prompts = len({p for p in prompts if p})
    st.top_prompts = Counter(p for p in prompts if p).most_common(5)

    st.distinct_categories = len({i.get("category") for i in items})
    st.distinct_moods = len({i.get("mood") for i in items})

    lengths = [len(i.get("text") or "") for i in items]
    if lengths:
        st.length_stats = {"min": min(lengths), "max": max(lengths),
                           "avg": round(sum(lengths) / len(lengths), 1)}

    ordered = publication_order(items, st.content_type)
    st.longest_category_run = _longest_run([i.get("category") for i in ordered])
    st.longest_mood_run = _longest_run([i.get("mood") for i in ordered])
    st.findings = [f.as_dict() for f in _window_findings(ordered)]

    for f in st.findings:
        st.warnings.append(
            f"{f['kind']}: «{f['value'][:60]}» compare {f['count']} volte in "
            f"{f['window']} giorni consecutivi (dall'indice {f['start_index']})")
    if st.longest_category_run > MAX_SAME_CATEGORY_RUN:
        st.warnings.append(
            f"categoria ripetuta per {st.longest_category_run} giorni di fila "
            f"(massimo {MAX_SAME_CATEGORY_RUN})")
    if st.longest_mood_run > MAX_SAME_MOOD_RUN:
        st.warnings.append(
            f"mood ripetuto per {st.longest_mood_run} giorni di fila "
            f"(massimo {MAX_SAME_MOOD_RUN})")
    if st.duplicate_caption_bodies:
        st.warnings.append(
            f"{st.duplicate_caption_bodies} didascalie hanno un corpo identico "
            f"(stessa frase per contenuti diversi)")
    return st


def analyse_all(datasets_dir: str | Path) -> list[DatasetStats]:
    return [analyse_dataset(p) for p in sorted(Path(datasets_dir).glob("*.json"))]


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------
def _stable_rank(seed: int, key: str) -> int:
    h = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def _strata(item: dict, index: int, total: int) -> list[str]:
    """Every stratum an item belongs to. An item usually belongs to several."""
    out = ["all"]
    third = max(1, total // 3)
    out.append("inizio" if index < third
               else "fine" if index >= total - third else "centro")
    out.append(f"categoria:{item.get('category') or '—'}")
    out.append(f"mood:{item.get('mood') or '—'}")
    out.append(f"fonte:{item.get('source_name') or '—'}")
    out.append(f"audit:{item.get('source_audit_status') or 'not_checked'}")
    if item.get("calendar_key"):
        out.append(f"mese:{str(item['calendar_key'])[:2]}")
    text = item.get("text") or ""
    if _NUMBER_RE.search(text):
        out.append("con_numeri")
    if _ABSOLUTE_RE.search(text):
        out.append("affermazione_assoluta")
    length = len(text)
    out.append("corto" if length < 70 else "lungo" if length > 130 else "medio")
    # The authoring batch, from the numeric part of the id ("wc-0247-…" -> 2).
    # The id keeps the original build order even after sequence_index is
    # re-spread, so this is what tells you whether the sample reaches the blocks
    # written last. Taking the id verbatim would give every item its own
    # stratum of size one — which is exactly the bug that made the first sample
    # 100 consecutive items instead of a spread.
    parts = str(item.get("id") or "").split("-")
    if len(parts) > 1 and parts[1].isdigit():
        out.append(f"batch:{int(parts[1]) // 100:02d}")
    return out


def stratified_sample(items: list[dict], *, count: int, seed: int) -> list[int]:
    """Indexes of ``count`` items covering every stratum, deterministically.

    Round-robins over the strata taking each one's best-ranked unused item, so
    small strata (a rare category, the ``02-29`` events, the absolute claims)
    are represented instead of being drowned by the common ones.
    """
    total = len(items)
    if count >= total:
        return list(range(total))

    buckets: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(items):
        for stratum in _strata(item, i, total):
            buckets[stratum].append(i)

    for stratum, idxs in buckets.items():
        idxs.sort(key=lambda i: _stable_rank(seed, f"{stratum}|{i}"))

    ordered_strata = sorted(buckets, key=lambda s: (len(buckets[s]), s))
    chosen: list[int] = []
    taken: set[int] = set()
    cursor = {s: 0 for s in ordered_strata}

    while len(chosen) < count:
        progressed = False
        for stratum in ordered_strata:
            if len(chosen) >= count:
                break
            idxs = buckets[stratum]
            while cursor[stratum] < len(idxs) and idxs[cursor[stratum]] in taken:
                cursor[stratum] += 1
            if cursor[stratum] < len(idxs):
                idx = idxs[cursor[stratum]]
                cursor[stratum] += 1
                taken.add(idx)
                chosen.append(idx)
                progressed = True
        if not progressed:
            break
    return sorted(chosen)
