"""Standalone validation of the evergreen dataset files.

This runs **before** anything touches the database, so a bad dataset can never
reach production. It is the engine behind ``python -m src.cli validate-datasets``,
which exits non-zero if a single blocking requirement is unmet.

Checks per dataset:

* exactly ``expected_items`` entries (1.000 for the five evergreen pages);
* no empty ``text``/``caption``;
* ``sequence_index`` unique and covering ``0..N-1`` with no gaps (cyclic types);
* exact, fuzzy and semantic duplicate detection;
* text lengths compatible with the rendering templates;
* fact-checked types: ``verification_status: verified`` + source name + a
  formally valid URL + an ISO ``verified_at`` date;
* ``today_in_history``: every item has a valid ``MM-DD`` ``calendar_key``, all
  366 keys are covered, and each key has at least two events;
* hashtags present and within a sane count.

The JSON report additionally carries counts per category, length statistics and
the calendar distribution — useful even when everything passes.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path

from .dedup import SimilarityIndex
from .importer import (CALENDAR_TYPES, CYCLIC_TYPES, FACT_CHECKED_TYPES,
                       ORIGINAL_TYPES, valid_calendar_key, valid_url)
from .language_check import ERROR as LANG_ERROR
from .language_check import check_item
from .normalize import content_hash, normalize_text
from .quality import LENGTH_PROFILES, score_content

#: Every possible ``MM-DD`` key, 29 February included (366 entries).
ALL_CALENDAR_KEYS: tuple[str, ...] = tuple(
    f"{m:02d}-{d:02d}"
    for m in range(1, 13)
    for d in range(1, (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[m - 1] + 1)
)

DEFAULT_EXPECTED_ITEMS = 1000
MIN_EVENTS_PER_CALENDAR_KEY = 2
MIN_HASHTAGS, MAX_HASHTAGS = 3, 10
MAX_CAPTION_CHARS = 2200          # Meta caption limit


@dataclass
class DatasetReport:
    dataset: str
    content_type: str = ""
    total: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)
    by_mood: dict[str, int] = field(default_factory=dict)
    exact_duplicates: list[str] = field(default_factory=list)
    near_duplicates: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    calendar_distribution: dict[str, int] = field(default_factory=dict)
    length_stats: dict[str, float] = field(default_factory=dict)
    caption_stats: dict[str, float] = field(default_factory=dict)
    quality_stats: dict[str, float] = field(default_factory=dict)
    sources: dict[str, int] = field(default_factory=dict)
    language_issues: list[dict] = field(default_factory=list)
    language_counts: dict[str, int] = field(default_factory=dict)
    source_audit: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "content_type": self.content_type,
            "total": self.total,
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "counts_by_category": self.by_category,
            "counts_by_mood": self.by_mood,
            "exact_duplicates": self.exact_duplicates,
            "near_duplicates": self.near_duplicates,
            "unverified_items": self.unverified,
            "calendar_distribution": self.calendar_distribution,
            "text_length": self.length_stats,
            "caption_length": self.caption_stats,
            "quality": self.quality_stats,
            "sources": self.sources,
            "language_issues": self.language_issues,
            "language_counts": self.language_counts,
            "source_audit": self.source_audit,
        }


def _stats(values: list[int | float]) -> dict[str, float]:
    if not values:
        return {"min": 0, "avg": 0, "max": 0, "count": 0}
    return {
        "min": round(min(values), 3),
        "avg": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
        "count": len(values),
    }


def _check_sequence(items: list[dict], expected: int, rep: DatasetReport) -> None:
    seen: dict[int, int] = {}
    missing_field: list[int] = []
    for i, item in enumerate(items):
        idx = item.get("sequence_index")
        if idx is None or not isinstance(idx, int):
            missing_field.append(i)
            continue
        if idx in seen:
            rep.errors.append(
                f"sequence_index duplicato {idx} (item #{i} e #{seen[idx]})")
        seen[idx] = i
    if missing_field:
        rep.errors.append(
            f"{len(missing_field)} elementi senza sequence_index intero "
            f"(primi: {missing_field[:5]})")
    holes = sorted(set(range(expected)) - set(seen))
    if holes:
        rep.errors.append(
            f"sequence_index mancanti fra 0 e {expected - 1}: "
            f"{len(holes)} buchi (primi: {holes[:10]})")
    extra = sorted(k for k in seen if k < 0 or k >= expected)
    if extra:
        rep.errors.append(
            f"sequence_index fuori intervallo 0..{expected - 1}: {extra[:10]}")


def _check_calendar(items: list[dict], rep: DatasetReport) -> None:
    dist: Counter = Counter()
    for i, item in enumerate(items):
        key = item.get("calendar_key")
        if not valid_calendar_key(key):
            rep.errors.append(f"item #{i}: calendar_key non valido ({key!r})")
            continue
        dist[str(key)] += 1
        meta = item.get("metadata") or {}
        year = str(meta.get("year") or "").strip()
        if not year:
            rep.errors.append(f"item #{i} ({key}): metadata.year mancante")
        else:
            try:
                y = int(year)
                if not -3000 <= y <= date_cls.today().year:
                    rep.errors.append(f"item #{i}: anno {y} implausibile")
            except ValueError:
                rep.errors.append(f"item #{i}: metadata.year {year!r} non numerico")
    rep.calendar_distribution = dict(sorted(dist.items()))

    empty = [k for k in ALL_CALENDAR_KEYS if dist.get(k, 0) == 0]
    if empty:
        rep.errors.append(
            f"{len(empty)} date senza alcun evento (prime: {empty[:10]})")
    thin = [k for k in ALL_CALENDAR_KEYS
            if 0 < dist.get(k, 0) < MIN_EVENTS_PER_CALENDAR_KEY]
    if thin:
        rep.errors.append(
            f"{len(thin)} date con meno di {MIN_EVENTS_PER_CALENDAR_KEY} eventi "
            f"(prime: {thin[:10]})")
    if dist.get("02-29", 0) < MIN_EVENTS_PER_CALENDAR_KEY:
        rep.errors.append("il 29 febbraio deve avere almeno due eventi")


def _check_verification(items: list[dict], content_type: str,
                        rep: DatasetReport) -> None:
    sources: Counter = Counter()
    for i, item in enumerate(items):
        problems: list[str] = []
        if (item.get("verification_status") or "").lower() != "verified":
            problems.append("verification_status != verified")
        name = (item.get("source_name") or "").strip()
        if not name:
            problems.append("source_name mancante")
        else:
            sources[name] += 1
        if not valid_url(item.get("source_url")):
            problems.append(f"source_url non valido ({item.get('source_url')!r})")
        va = str(item.get("verified_at") or "")
        try:
            date_cls.fromisoformat(va)
        except ValueError:
            problems.append(f"verified_at non valido ({va!r})")
        if problems:
            rep.unverified.append(f"item #{i}: " + "; ".join(problems))
    if rep.unverified:
        rep.errors.append(
            f"{len(rep.unverified)} elementi di tipo {content_type!r} non "
            f"verificati o senza fonte completa")
    rep.sources = dict(sources.most_common())


def _check_originality(items: list[dict], content_type: str,
                       rep: DatasetReport) -> None:
    for i, item in enumerate(items):
        if content_type == "philosophical_thought" and (
                item.get("author") or item.get("author_display_name")
                or item.get("source_work")):
            rep.errors.append(
                f"item #{i}: un pensiero originale non può avere attribuzione")
        if content_type == "daily_question" and not (
                item.get("text") or "").strip().endswith("?"):
            rep.errors.append(f"item #{i}: la domanda non termina con '?'")


def _check_history(items: list[dict], rep: DatasetReport) -> None:
    """Date/year/title consistency (see :mod:`src.content.history_check`)."""
    from .history_check import ERROR as H_ERROR
    from .history_check import check_dataset as check_history

    errors: list[str] = []
    warnings: Counter = Counter()
    for idx, issues in check_history(items).items():
        for issue in issues:
            label = f"item #{idx} [{issue.rule}] {issue.message}"
            if issue.severity == H_ERROR:
                errors.append(label)
            else:
                warnings[issue.rule] += 1
    if errors:
        rep.errors.append(f"{len(errors)} incoerenze di data/anno: "
                          + "; ".join(errors[:5]))
        rep.language_issues.extend({"rule": "history", "severity": "error",
                                    "field": "metadata", "message": e,
                                    "excerpt": ""} for e in errors)
    for rule, n in warnings.most_common():
        rep.warnings.append(f"{n} eventi da rivedere: {rule}")


def _check_words(items: list[dict], rep: DatasetReport) -> None:
    """Lemma/part-of-speech/source consistency (see :mod:`src.content.word_check`)."""
    from .word_check import ERROR as W_ERROR
    from .word_check import check_dataset as check_words

    errors: list[str] = []
    warnings: Counter = Counter()
    for idx, issues in check_words(items).items():
        for issue in issues:
            if issue.severity == W_ERROR:
                errors.append(f"item #{idx} [{issue.rule}] {issue.message}")
            else:
                warnings[issue.rule] += 1
    if errors:
        rep.errors.append(f"{len(errors)} incoerenze sui lemmi: "
                          + "; ".join(errors[:5]))
    for rule, n in warnings.most_common():
        rep.warnings.append(f"{n} lemmi da rivedere: {rule}")


def validate_dataset(path: str | Path, *, expected_items: int = DEFAULT_EXPECTED_ITEMS,
                     fuzzy_threshold: float = 0.88,
                     semantic_threshold: float = 0.82,
                     check_quality: bool = True) -> DatasetReport:
    path = Path(path)
    rep = DatasetReport(dataset=path.name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        rep.errors.append(f"impossibile leggere il dataset: {e}")
        return rep

    content_type = data.get("content_type") or ""
    rep.content_type = content_type
    items = data.get("items") or []
    rep.total = len(items)
    if not content_type:
        rep.errors.append("campo 'content_type' mancante nel dataset")
    if rep.total != expected_items:
        rep.errors.append(
            f"attesi {expected_items} elementi, trovati {rep.total}")

    # -- per-item structural checks ------------------------------------
    hashes: dict[str, int] = {}
    lengths: list[int] = []
    caption_lengths: list[int] = []
    qualities: list[float] = []
    categories: Counter = Counter()
    moods: Counter = Counter()
    lo, hi, hard_max = LENGTH_PROFILES.get(content_type,
                                           LENGTH_PROFILES["motivational"])

    lang_counts: Counter = Counter()
    audit_counts: Counter = Counter()

    index = SimilarityIndex(fuzzy_threshold=fuzzy_threshold,
                            semantic_threshold=semantic_threshold)
    dedup_fuzzy = content_type != "word_of_the_day"

    for i, item in enumerate(items):
        text = (item.get("text") or "").strip()
        if not text:
            rep.errors.append(f"item #{i}: 'text' vuoto")
            continue
        lengths.append(len(text))
        if len(text) > hard_max:
            rep.errors.append(
                f"item #{i}: testo di {len(text)} caratteri > limite di "
                f"rendering {hard_max}")
        elif len(text) > hi:
            rep.warnings.append(f"item #{i}: testo lungo ({len(text)} caratteri)")
        elif len(text) < lo:
            rep.warnings.append(f"item #{i}: testo corto ({len(text)} caratteri)")

        h = content_hash(text)
        if h in hashes:
            rep.exact_duplicates.append(
                f"item #{i} identico a #{hashes[h]}: {text[:60]!r}")
        else:
            hashes[h] = i
            if dedup_fuzzy:
                dup, match = index.is_duplicate(text)
                if dup:
                    rep.near_duplicates.append(
                        f"item #{i} ~ #{match.id} ({match.kind} {match.score}): "
                        f"{text[:60]!r}")
                index.add(i, text)

        caption = (item.get("caption") or "").strip()
        if not caption:
            rep.errors.append(f"item #{i}: 'caption' vuota")
        else:
            caption_lengths.append(len(caption))
            if len(caption) > MAX_CAPTION_CHARS:
                rep.errors.append(
                    f"item #{i}: didascalia di {len(caption)} caratteri "
                    f"> limite Meta {MAX_CAPTION_CHARS}")
            if normalize_text(caption) == normalize_text(text):
                rep.warnings.append(f"item #{i}: la didascalia ripete solo il testo")

        tags = item.get("hashtags") or []
        if not isinstance(tags, list) or not (MIN_HASHTAGS <= len(tags) <= MAX_HASHTAGS):
            rep.errors.append(
                f"item #{i}: servono da {MIN_HASHTAGS} a {MAX_HASHTAGS} hashtag "
                f"(trovati {len(tags) if isinstance(tags, list) else 'non-lista'})")
        elif len(set(tags)) != len(tags):
            rep.errors.append(f"item #{i}: hashtag duplicati")

        if not (item.get("background_prompt") or "").strip():
            rep.errors.append(f"item #{i}: 'background_prompt' mancante")

        categories[item.get("category") or "—"] += 1
        moods[item.get("mood") or "—"] += 1
        audit_counts[item.get("source_audit_status") or "not_checked"] += 1

        # -- language: the checks that catch what a proof-reader misses on the
        # thousandth item (see src/content/language_check.py).
        for issue in check_item(item, content_type=content_type):
            lang_counts[issue.rule] += 1
            record = {"item": i, **issue.as_dict()}
            if issue.severity == LANG_ERROR:
                rep.language_issues.append(record)
            elif len(rep.language_issues) < 400:
                rep.language_issues.append(record)

        if check_quality:
            qualities.append(score_content(text, content_type=content_type,
                                           caption=item.get("caption")).score)

    if rep.exact_duplicates:
        rep.errors.append(f"{len(rep.exact_duplicates)} duplicati esatti")
    if rep.near_duplicates:
        rep.errors.append(
            f"{len(rep.near_duplicates)} quasi-duplicati (fuzzy/semantici)")

    # -- type-specific checks -------------------------------------------
    if content_type in CYCLIC_TYPES:
        _check_sequence(items, expected_items, rep)
    if content_type in CALENDAR_TYPES:
        _check_calendar(items, rep)
        _check_sequence(items, expected_items, rep)
    if content_type in FACT_CHECKED_TYPES:
        _check_verification(items, content_type, rep)
    if content_type in ORIGINAL_TYPES:
        _check_originality(items, content_type, rep)
    if content_type == "today_in_history":
        _check_history(items, rep)
    if content_type == "word_of_the_day":
        _check_words(items, rep)

    # A language error is blocking: it is certainly wrong and it renders into
    # the image. Warnings (an absolute claim, a lowercase opening) are reported
    # for the editorial review and never fail the build.
    lang_errors = sum(1 for r in rep.language_issues if r["severity"] == LANG_ERROR)
    if lang_errors:
        by_rule = Counter(r["rule"] for r in rep.language_issues
                          if r["severity"] == LANG_ERROR)
        rep.errors.append(
            f"{lang_errors} errori di lingua: " +
            ", ".join(f"{k}×{v}" for k, v in by_rule.most_common()))
    lang_warnings = len(rep.language_issues) - lang_errors
    if lang_warnings:
        rep.warnings.append(
            f"{lang_warnings} segnalazioni linguistiche non bloccanti "
            f"(vedi 'language_issues' nel report)")

    rep.language_counts = dict(lang_counts.most_common())
    rep.source_audit = dict(audit_counts.most_common())
    if content_type in FACT_CHECKED_TYPES:
        # A dead source is a defect, but a *known* dead source that is already
        # blocked from production is a defect being managed, not a build break.
        # What must never happen is an item that is publishable **and** has a
        # source nobody can open — that is the invariant worth failing on.
        from .editorial import is_production_ready

        leaking = [
            i for i, item in enumerate(items)
            if (item.get("source_audit_status") or "") in
               ("broken_source", "unsupported", "not_checked")
            and is_production_ready(
                content_type, status=item.get("status") or "",
                verification_status=item.get("verification_status"),
                source_audit_status=item.get("source_audit_status"),
                editorial_status=item.get("editorial_status"))[0]
        ]
        if leaking:
            rep.errors.append(
                f"{len(leaking)} elementi pubblicabili con fonte non "
                f"verificata (primi: {leaking[:5]})")
        broken = audit_counts.get("broken_source", 0)
        if broken:
            rep.warnings.append(
                f"{broken} elementi con fonte non raggiungibile: bloccati alla "
                f"pubblicazione, da correggere o sostituire")
        unchecked = audit_counts.get("not_checked", 0)
        if unchecked:
            rep.warnings.append(
                f"{unchecked} elementi con fonte mai controllata: esegui "
                f"'python -m src.cli audit-sources'")

    rep.by_category = dict(categories.most_common())
    rep.by_mood = dict(moods.most_common())
    rep.length_stats = _stats(lengths)
    rep.caption_stats = _stats(caption_lengths)
    rep.quality_stats = _stats(qualities)
    return rep


@dataclass
class ValidationSummary:
    reports: list[DatasetReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reports)

    @property
    def total_items(self) -> int:
        return sum(r.total for r in self.reports)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "datasets": len(self.reports),
            "total_items": self.total_items,
            "items_per_dataset": {r.dataset: r.total for r in self.reports},
            "blocking_errors": sum(len(r.errors) for r in self.reports),
            "warnings": sum(len(r.warnings) for r in self.reports),
            "reports": [r.as_dict() for r in self.reports],
        }


def validate_all(datasets_dir: str | Path, *,
                 expected_items: int = DEFAULT_EXPECTED_ITEMS,
                 check_quality: bool = True) -> ValidationSummary:
    """Validate every ``*.json`` directly inside ``datasets_dir`` (not ``_archive``)."""
    summary = ValidationSummary()
    for path in sorted(Path(datasets_dir).glob("*.json")):
        summary.reports.append(
            validate_dataset(path, expected_items=expected_items,
                             check_quality=check_quality))
    if not summary.reports:
        rep = DatasetReport(dataset="(nessuno)")
        rep.errors.append(f"nessun dataset trovato in {datasets_dir}")
        summary.reports.append(rep)
    return summary


def write_report(summary: ValidationSummary, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
