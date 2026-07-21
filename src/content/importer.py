"""Import dataset JSON files into the DB with dedup + quality gating.

Rules:
- exact duplicate (same normalized hash) → skipped.
- near duplicate (fuzzy/semantic above threshold) → skipped, reason recorded.
- quality below the page/import floor → stored as ``needs_review`` (never lost).
- motivational: high quality → ``approved_for_publication``.
- famous_quote: only ``verified`` + ``high`` attribution → ``approved_for_publication``;
  everything else → ``needs_review`` (uncertain attributions never auto-publish).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.enums import ContentStatus
from ..database import Database
from .dedup import SimilarityIndex
from .normalize import content_hash, normalize_text
from .quality import score_content


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


def _decide_status(content_type: str, item: dict, quality: float,
                   approve_floor: float) -> str:
    if content_type == "famous_quote":
        ds_status = (item.get("status") or "").lower()
        conf = (item.get("attribution_confidence") or "").lower()
        has_source = bool(item.get("source_url"))
        if ds_status == "verified" and conf == "high" and has_source and quality >= 0.6:
            return ContentStatus.APPROVED_FOR_PUBLICATION
        if ds_status == "rejected":
            return ContentStatus.REJECTED
        return ContentStatus.NEEDS_REVIEW
    # motivational / other original content
    if quality >= approve_floor:
        return ContentStatus.APPROVED_FOR_PUBLICATION
    if quality >= 0.5:
        return ContentStatus.NEEDS_REVIEW
    return ContentStatus.NEEDS_REVIEW


def import_dataset(
    db: Database,
    dataset_path: str | Path,
    *,
    approve_floor: float = 0.80,
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

    # Seed the similarity index with existing DB content of this type.
    index = SimilarityIndex(
        fuzzy_threshold=fuzzy_threshold, semantic_threshold=semantic_threshold
    )
    for row in db.content_texts(content_type):
        index.add(row["id"], row["text"])

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

        dup, match = index.is_duplicate(text)
        if dup:
            report.near_duplicates += 1
            report.messages.append(
                f"item #{i} near-dup ({match.kind} {match.score}) of id={match.id}: {text[:48]!r}"
            )
            continue

        q = score_content(
            text,
            content_type=content_type,
            caption=item.get("caption"),
            language=language,
            use_languagetool=use_languagetool,
        )
        status = _decide_status(content_type, item, q.score, approve_floor)

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
