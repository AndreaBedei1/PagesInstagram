"""The single check that says whether the corpus may go to production.

Everything else reports; this decides. It counts the twelve ways a corpus can be
unfit and fails if any count is above zero, so there is one number to look at
before a release rather than five reports to correlate.

It deliberately re-derives rather than trusts:

* the hash is recomputed from the stored text, so an item edited after
  verification fails here even though its stored status still says ``verified``;
* duplicates are recomputed over the current text, not read from a field;
* the language and calendar checks run again on the shipped strings.

None of it touches the network. The online work happens once, during the
rebuild; this is the deterministic gate that CI can run on every push.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .dataset_io import load_dataset
from .dedup import SimilarityIndex
from .history_check import ERROR as H_ERROR
from .history_check import calendar_coverage
from .history_check import check_dataset as check_history
from .language_check import ERROR as L_ERROR
from .language_check import check_item
from .normalize import content_hash
from .verification import (FACTUAL_TYPES, claim_hash, is_publishable)
from .word_check import ERROR as W_ERROR
from .word_check import check_dataset as check_words

EXPECTED_PER_DATASET = 1000
EXPECTED_DATASETS = 5
EXPECTED_CALENDAR_KEYS = 366


@dataclass
class DatasetGate:
    dataset: str
    content_type: str = ""
    items: int = 0
    production_ready: int = 0
    not_checked: int = 0
    needs_review: int = 0
    blocked: int = 0
    broken_source: int = 0
    weak_source: int = 0
    hash_mismatch: int = 0
    duplicate_exact: int = 0
    duplicate_semantic: int = 0
    language_errors: int = 0
    unsupported_claims: int = 0
    invalid_calendar_keys: int = 0
    invalid_words: int = 0
    wrong_item_count: int = 0
    detail: list = field(default_factory=list)

    #: Every counter that must be zero.
    FAILURE_FIELDS = ("not_checked", "needs_review", "blocked", "broken_source",
                      "weak_source", "hash_mismatch", "duplicate_exact",
                      "duplicate_semantic", "language_errors",
                      "unsupported_claims", "invalid_calendar_keys",
                      "invalid_words", "wrong_item_count")

    @property
    def failures(self) -> int:
        return sum(getattr(self, f) for f in self.FAILURE_FIELDS)

    @property
    def ok(self) -> bool:
        return self.failures == 0 and self.production_ready == self.items

    def as_dict(self) -> dict:
        data = asdict(self)
        data["failures"] = self.failures
        data["ok"] = self.ok
        data["detail"] = self.detail[:25]
        return data


def gate_dataset(path: str | Path) -> DatasetGate:
    path = Path(path)
    data = load_dataset(path)
    items = data.get("items") or []
    ctype = data.get("content_type") or ""
    g = DatasetGate(dataset=path.name, content_type=ctype, items=len(items))

    if len(items) != EXPECTED_PER_DATASET:
        g.wrong_item_count = 1
        g.detail.append(f"{len(items)} elementi invece di {EXPECTED_PER_DATASET}")

    hashes: dict[str, int] = {}
    index = SimilarityIndex(fuzzy_threshold=0.88, semantic_threshold=0.82)
    fuzzy = ctype != "word_of_the_day"

    for i, item in enumerate(items):
        text = item.get("text") or ""
        result = is_publishable(item, content_type=ctype)
        if result.ok:
            g.production_ready += 1
        else:
            joined = "; ".join(result.reasons)
            if "verified_content_hash non corrisponde" in joined:
                g.hash_mismatch += 1
            elif "verification_method" in joined and "non riconosciuto" in joined:
                g.not_checked += 1
            elif "evidence_summary" in joined:
                g.unsupported_claims += 1
            elif "source_strength" in joined:
                g.weak_source += 1
            elif "source_url" in joined:
                g.broken_source += 1
            else:
                g.blocked += 1
            g.detail.append(f"#{item.get('sequence_index')}: {joined[:110]}")

        # Hash recomputed, never trusted.
        stored = (item.get("verified_content_hash") or "").strip()
        if stored and stored != claim_hash(text):
            g.hash_mismatch += 1

        h = content_hash(text)
        if h in hashes:
            g.duplicate_exact += 1
            g.detail.append(f"#{i} duplicato esatto di #{hashes[h]}")
        else:
            hashes[h] = i
            if fuzzy:
                dup, match = index.is_duplicate(text)
                if dup:
                    g.duplicate_semantic += 1
                    g.detail.append(
                        f"#{i} ~ #{match.id} ({match.kind} {match.score})")
                else:
                    index.add(i, text)

        for issue in check_item(item, content_type=ctype):
            if issue.severity == L_ERROR:
                g.language_errors += 1
                g.detail.append(f"#{i} {issue.rule}: {issue.message[:80]}")

        if ctype in FACTUAL_TYPES:
            status = (item.get("source_audit_status") or "").strip()
            if status in ("broken_source", "unsupported"):
                # Only counts when it still blocks: a replaced item carries a
                # stale audit note from the corpus it replaced.
                if not result.ok:
                    g.broken_source += 1

    if ctype == "today_in_history":
        for idx, issues in check_history(items).items():
            for issue in issues:
                if issue.severity == H_ERROR:
                    g.invalid_calendar_keys += 1
                    g.detail.append(f"#{idx} {issue.rule}: {issue.message[:80]}")
        coverage = calendar_coverage(items)
        if len(coverage) != EXPECTED_CALENDAR_KEYS:
            g.invalid_calendar_keys += 1
            g.detail.append(
                f"{len(coverage)} date coperte invece di {EXPECTED_CALENDAR_KEYS}")
        thin = [k for k, n in coverage.items() if n < 2]
        if thin:
            g.invalid_calendar_keys += len(thin)
            g.detail.append(f"{len(thin)} date con meno di due eventi: {thin[:6]}")

    if ctype == "word_of_the_day":
        for idx, issues in check_words(items).items():
            for issue in issues:
                if issue.severity == W_ERROR:
                    g.invalid_words += 1
                    g.detail.append(f"#{idx} {issue.rule}: {issue.message[:80]}")

    return g


@dataclass
class FinalGate:
    datasets: list = field(default_factory=list)

    @property
    def items(self) -> int:
        return sum(g.items for g in self.datasets)

    @property
    def production_ready(self) -> int:
        return sum(g.production_ready for g in self.datasets)

    @property
    def failures(self) -> int:
        missing = max(0, EXPECTED_DATASETS - len(self.datasets))
        return sum(g.failures for g in self.datasets) + missing

    @property
    def ok(self) -> bool:
        return (self.failures == 0
                and len(self.datasets) == EXPECTED_DATASETS
                and self.production_ready == self.items
                and self.items == EXPECTED_DATASETS * EXPECTED_PER_DATASET)

    def counters(self) -> dict:
        out: Counter = Counter()
        for g in self.datasets:
            for field_name in DatasetGate.FAILURE_FIELDS:
                out[field_name] += getattr(g, field_name)
        return dict(out)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "items": self.items,
                "production_ready": self.production_ready,
                "failures": self.failures, "counters": self.counters(),
                "datasets": [g.as_dict() for g in self.datasets]}


def run_final_gate(datasets_dir: str | Path) -> FinalGate:
    gate = FinalGate()
    for path in sorted(Path(datasets_dir).glob("*.json")):
        gate.datasets.append(gate_dataset(path))
    return gate


def write_report(gate: FinalGate, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gate.as_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
