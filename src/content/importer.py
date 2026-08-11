"""Import dataset JSON files into the DB with dedup + quality + provenance gating.

Rules:
- exact duplicate (same normalized hash) → skipped.
- near duplicate (fuzzy/semantic above threshold) → skipped, reason recorded.
- quality below the type's approval floor → stored as ``needs_review`` (never lost).

Per content type:

===========================  ==========================================================
type                         approval rule
===========================  ==========================================================
``motivational``             original text, quality ≥ floor
``philosophical_thought``    original text, quality ≥ floor, **no attribution allowed**
``daily_question``           original text, quality ≥ floor, must be a question
``famous_quote``             ``verified`` + ``high`` confidence + source URL
``world_curiosity``          ``verification_status: verified`` + source name + URL + date
``word_of_the_day``          idem (lexicographic source)
``today_in_history``         idem, **plus** a valid ``calendar_key`` and a year
===========================  ==========================================================

Nothing factual is ever auto-approved without a source: the three fact-checked
types fall back to ``needs_review`` so a human sees them in the dashboard.

**What ``approved_for_publication`` does and does not mean.** It is a *structural*
verdict: the item is well formed, non-duplicate, long enough to render, and
carries the provenance fields its type requires. It is **not** evidence that the
source exists, that it supports the claim, or that anyone read the text. Those
live on separate axes (``source_audit_status``, ``editorial_status``, see
:mod:`src.content.editorial`) which this importer only ever *copies* from the
dataset — it can never promote an item to a human verdict. Production
publication requires all of them, so a dataset full of structurally valid
content still publishes nothing until it is reviewed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from urllib.parse import urlparse

from ..core.enums import ContentStatus
from ..database import Database
from .dedup import SimilarityIndex
from .normalize import content_hash, normalize_text
from .quality import score_content

#: Content types whose items must carry a verifiable source to be published.
FACT_CHECKED_TYPES = frozenset({"world_curiosity", "word_of_the_day",
                                "today_in_history"})

#: Content types that are original writing (no attribution, no external source).
ORIGINAL_TYPES = frozenset({"philosophical_thought", "daily_question",
                            "motivational"})

#: Content types driven by the linear 1.000-day cycle.
CYCLIC_TYPES = frozenset({"philosophical_thought", "world_curiosity",
                          "word_of_the_day", "daily_question"})

#: Content types driven by the calendar (``MM-DD``).
CALENDAR_TYPES = frozenset({"today_in_history"})

#: Approval floor per content type (the generic default stays 0.80).
TYPE_APPROVAL_FLOOR: dict[str, float] = {
    "philosophical_thought": 0.72,
    "daily_question": 0.70,
    "world_curiosity": 0.60,
    "word_of_the_day": 0.55,
    "today_in_history": 0.55,
}

_CALENDAR_KEY_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Words that would make an "original" thought look like a (fake) quotation.
_ATTRIBUTION_MARKERS = ("come diceva", "come scriveva", "citazione di",
                        "secondo il filosofo", "diceva sempre")


@dataclass
class ImportReport:
    dataset: str
    content_type: str = ""
    total: int = 0
    added: int = 0
    approved: int = 0
    needs_review: int = 0
    exact_duplicates: int = 0
    near_duplicates: int = 0
    errors: int = 0
    clusters_marked: int = 0
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.dataset}: total={self.total} added={self.added} "
            f"(approved={self.approved}, needs_review={self.needs_review}) "
            f"exact_dup={self.exact_duplicates} near_dup={self.near_duplicates} "
            f"errors={self.errors} clusters={self.clusters_marked}"
        )


def valid_url(url: str | None) -> bool:
    """Formal validation only — no network call at import time."""
    if not url:
        return False
    try:
        p = urlparse(str(url))
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc) and "." in p.netloc


def valid_calendar_key(key: str | None) -> bool:
    """``MM-DD`` in 01-01..12-31, including 02-29."""
    if not key or not _CALENDAR_KEY_RE.match(str(key)):
        return False
    mm, dd = (int(x) for x in str(key).split("-"))
    max_day = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[mm - 1]
    return 1 <= dd <= max_day


def _verification_issues(content_type: str, item: dict) -> list[str]:
    """Reasons a fact-checked item may NOT be auto-approved (empty = fine)."""
    issues: list[str] = []
    if (item.get("verification_status") or "").lower() != "verified":
        issues.append("verification_status != 'verified'")
    if not (item.get("source_name") or "").strip():
        issues.append("source_name mancante")
    if not valid_url(item.get("source_url")):
        issues.append("source_url mancante o non valido")
    verified_at = str(item.get("verified_at") or "")
    if not _ISO_DATE_RE.match(verified_at):
        issues.append("verified_at mancante o non in formato YYYY-MM-DD")
    else:
        try:
            date_cls.fromisoformat(verified_at)
        except ValueError:
            issues.append("verified_at non è una data reale")
    if content_type == "today_in_history":
        if not valid_calendar_key(item.get("calendar_key")):
            issues.append("calendar_key mancante o non valido (atteso MM-DD)")
        meta = item.get("metadata") or {}
        if not str(meta.get("year") or "").strip():
            issues.append("metadata.year mancante")
    return issues


def _original_issues(content_type: str, item: dict) -> list[str]:
    issues: list[str] = []
    text = (item.get("text") or "")
    if content_type == "philosophical_thought":
        if (item.get("author") or item.get("author_display_name")
                or item.get("source_work")):
            issues.append("un pensiero originale non può avere un autore attribuito")
        low = normalize_text(text)
        for marker in _ATTRIBUTION_MARKERS:
            if marker in low:
                issues.append(f"sembra una citazione attribuita ({marker!r})")
                break
    if content_type == "daily_question" and not text.strip().endswith("?"):
        issues.append("la domanda deve terminare con '?'")
    return issues


def _decide_status(content_type: str, item: dict, quality: float,
                   approve_floor: float) -> tuple[str, list[str]]:
    """Return ``(status, reasons)``. ``reasons`` explains a non-approval."""
    if (item.get("status") or "").lower() == "rejected":
        return ContentStatus.REJECTED, ["status=rejected nel dataset"]

    if content_type == "famous_quote":
        conf = (item.get("attribution_confidence") or "").lower()
        ds_status = (item.get("status") or "").lower()
        if (ds_status == "verified" and conf == "high"
                and valid_url(item.get("source_url")) and quality >= 0.6):
            return ContentStatus.APPROVED_FOR_PUBLICATION, []
        return ContentStatus.NEEDS_REVIEW, ["attribuzione non verificata"]

    if content_type in FACT_CHECKED_TYPES:
        reasons = _verification_issues(content_type, item)
        if quality < approve_floor:
            reasons.append(f"qualità {quality:.2f} < {approve_floor:.2f}")
        if reasons:
            return ContentStatus.NEEDS_REVIEW, reasons
        return ContentStatus.APPROVED_FOR_PUBLICATION, []

    reasons = _original_issues(content_type, item)
    if quality < approve_floor:
        reasons.append(f"qualità {quality:.2f} < {approve_floor:.2f}")
    if reasons:
        return ContentStatus.NEEDS_REVIEW, reasons
    return ContentStatus.APPROVED_FOR_PUBLICATION, []


def _verification_status(content_type: str, item: dict) -> str:
    if content_type in FACT_CHECKED_TYPES or content_type == "famous_quote":
        return (item.get("verification_status") or "unverified").lower()
    return "original"


def _editorial_columns(content_type: str, item: dict) -> dict:
    """Carry the dataset's audit fields into the row, defaulting to the honest value.

    A dataset that says nothing about an item gets ``not_checked`` — never
    ``manually_verified``. Nothing in this import path can promote an item to a
    human verdict; only :mod:`src.content.editorial_review` (driven by a person)
    writes those values into the dataset files.
    """
    from .editorial import (EditorialStatus, SourceAuditStatus, tier_for_host)
    from urllib.parse import urlparse

    src_status = (item.get("source_audit_status") or "").strip().lower()
    if src_status not in set(SourceAuditStatus):
        src_status = SourceAuditStatus.NOT_CHECKED.value
    ed_status = (item.get("editorial_status") or "").strip().lower()
    if ed_status not in set(EditorialStatus):
        ed_status = EditorialStatus.NOT_CHECKED.value

    tier = item.get("source_tier")
    if not tier and item.get("source_url"):
        try:
            tier = tier_for_host(urlparse(str(item["source_url"])).netloc)
        except ValueError:
            tier = None

    return {
        "source_audit_status": src_status,
        "source_audited_at": item.get("source_audited_at"),
        "source_audit_note": item.get("source_audit_note"),
        "editorial_status": ed_status,
        "editorial_note": item.get("editorial_note"),
        "source_tier": tier,
    }


def import_dataset(
    db: Database,
    dataset_path: str | Path,
    *,
    approve_floor: float | None = None,
    fuzzy_threshold: float = 0.88,
    semantic_threshold: float = 0.82,
    use_languagetool: bool = False,
) -> ImportReport:
    path = Path(dataset_path)
    report = ImportReport(dataset=path.name)
    data = json.loads(path.read_text(encoding="utf-8"))
    content_type = data.get("content_type") or "motivational"
    language = data.get("language") or "it"
    report.content_type = content_type
    items = data.get("items") or []
    report.total = len(items)

    floor = (approve_floor if approve_floor is not None
             else TYPE_APPROVAL_FLOOR.get(content_type, 0.80))

    # Seed the similarity index with existing DB content of this type.
    index = SimilarityIndex(
        fuzzy_threshold=fuzzy_threshold, semantic_threshold=semantic_threshold
    )
    for row in db.content_texts(content_type):
        index.add(row["id"], row["text"])

    # A single lemma per day: dedup on the lemma itself, not on fuzzy similarity
    # (many Italian words share a stem without being duplicates).
    dedup_fuzzy = content_type != "word_of_the_day"

    for i, item in enumerate(items):
        text = (item.get("text") or "").strip()
        if not text:
            report.errors += 1
            report.messages.append(f"item #{i}: manca 'text'")
            continue

        chash = content_hash(text)
        if db.hash_exists(chash):
            report.exact_duplicates += 1
            continue

        if dedup_fuzzy:
            dup, match = index.is_duplicate(text)
            if dup:
                report.near_duplicates += 1
                report.messages.append(
                    f"item #{i} near-dup ({match.kind} {match.score}) "
                    f"of id={match.id}: {text[:48]!r}"
                )
                continue

        q = score_content(
            text,
            content_type=content_type,
            caption=item.get("caption"),
            language=language,
            use_languagetool=use_languagetool,
        )
        status, reasons = _decide_status(content_type, item, q.score, floor)
        if reasons:
            report.messages.append(f"item #{i} ({text[:40]!r}): " + "; ".join(reasons))

        row = {
            "content_type": content_type,
            "language": item.get("language") or language,
            "text": text,
            "original_text": item.get("original_text"),
            "author": item.get("author"),
            "author_display_name": item.get("author_display_name") or item.get("author"),
            "source_work": item.get("source_work"),
            "source_year": item.get("source_year"),
            "source_url": item.get("source_url"),
            "source_name": item.get("source_name"),
            "attribution_confidence": item.get("attribution_confidence"),
            "category": item.get("category"),
            "mood": item.get("mood"),
            "explanation": item.get("explanation"),
            "caption": item.get("caption"),
            "hashtags": item.get("hashtags"),
            "call_to_action": item.get("call_to_action"),
            "background_prompt": item.get("background_prompt"),
            "normalized_text": normalize_text(text),
            "content_hash": chash,
            "quality_score": q.score,
            "status": status,
            # 0003 columns
            "sequence_index": item.get("sequence_index"),
            "calendar_key": item.get("calendar_key"),
            "metadata_json": item.get("metadata") or {},
            "verification_status": _verification_status(content_type, item),
            "verified_at": item.get("verified_at"),
            # 0004 columns
            **_editorial_columns(content_type, item),
            # 0005 columns: the evidence itself. Without these the datasets
            # carry a verification the database never sees, and every day of
            # the cycle resolves to a content the production gate then blocks.
            "verification_method": item.get("verification_method"),
            "evidence_summary": item.get("evidence_summary"),
            "source_title": item.get("source_title"),
            "source_checked_at": item.get("source_checked_at"),
            "source_strength": item.get("source_strength"),
            "verified_content_hash": item.get("verified_content_hash"),
            "verification_tool_version": item.get("verification_tool_version"),
            "verification_executor": item.get("verification_executor"),
        }
        new_id, inserted = db.insert_content(row)
        if not inserted:
            report.exact_duplicates += 1
            continue
        index.add(new_id, text)
        report.added += 1
        if status == ContentStatus.APPROVED_FOR_PUBLICATION:
            report.approved += 1
        elif status == ContentStatus.NEEDS_REVIEW:
            report.needs_review += 1

    if dedup_fuzzy:
        _mark_clusters(db, index, report)
    return report


def _mark_clusters(db: Database, index: SimilarityIndex, report: ImportReport) -> None:
    """Assign semantic_cluster ids so near-duplicates are traceable in review."""
    clusters = index.cluster()
    # Only mark items that actually share a cluster with at least one other item.
    from collections import Counter

    sizes = Counter(clusters.values())
    marked = 0
    for item_id, cluster_id in clusters.items():
        if sizes[cluster_id] > 1:
            db.update_content(item_id, semantic_cluster=cluster_id)
            marked += 1
    report.clusters_marked = marked
