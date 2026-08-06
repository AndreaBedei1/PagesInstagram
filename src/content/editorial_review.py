"""Record human review verdicts into the dataset files.

This is the only path that can set ``source_audit_status: manually_verified`` or
``editorial_status: approved``. No automatic step writes those values — that
separation is the whole point of the three-axis model: a machine can tell you a
URL answers, it cannot tell you the page supports the claim.

A verdict file is JSON, one entry per reviewed item::

    {
      "reviewer": "…",
      "reviewed_at": "2026-08-05",
      "method": "how the review was carried out",
      "verdicts": {
        "world_curiosities_it.json": {
          "224": {"source": "manually_verified", "editorial": "approved"},
          "631": {"source": "unsupported",
                  "note": "la fonte non sostiene il superlativo"}
        }
      }
    }

Only the items named are touched. Everything else keeps ``not_checked``, which
is what makes the counts in the final report honest: "137 verified" means 137
were read, not 137 passed a regex.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .dataset_io import load_dataset, save_dataset
from .editorial import EditorialStatus, SourceAuditStatus

#: Verdicts a reviewer may record for the source.
SOURCE_VERDICTS = {v.value for v in SourceAuditStatus}
#: Verdicts a reviewer may record for the text.
EDITORIAL_VERDICTS = {v.value for v in EditorialStatus}


@dataclass
class ApplyReport:
    dataset: str
    applied: int = 0
    skipped: list[str] = field(default_factory=list)
    by_source_verdict: dict[str, int] = field(default_factory=dict)
    by_editorial_verdict: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"dataset": self.dataset, "applied": self.applied,
                "skipped": self.skipped,
                "source_verdicts": self.by_source_verdict,
                "editorial_verdicts": self.by_editorial_verdict}


def validate_verdicts(payload: dict) -> list[str]:
    """Structural problems in a verdict file. Empty list = usable."""
    problems: list[str] = []
    if not str(payload.get("reviewer") or "").strip():
        problems.append("campo 'reviewer' mancante")
    if not str(payload.get("reviewed_at") or "").strip():
        problems.append("campo 'reviewed_at' mancante")
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, dict) or not verdicts:
        problems.append("campo 'verdicts' mancante o vuoto")
        return problems
    for dataset, entries in verdicts.items():
        if not isinstance(entries, dict):
            problems.append(f"{dataset}: 'verdicts' deve essere un oggetto")
            continue
        for key, entry in entries.items():
            if not str(key).lstrip("-").isdigit():
                problems.append(f"{dataset}: chiave {key!r} non è un sequence_index")
            src = entry.get("source")
            if src is not None and src not in SOURCE_VERDICTS:
                problems.append(f"{dataset}#{key}: verdetto fonte {src!r} non valido")
            ed = entry.get("editorial")
            if ed is not None and ed not in EDITORIAL_VERDICTS:
                problems.append(f"{dataset}#{key}: verdetto editoriale {ed!r} non valido")
    return problems


def apply_verdicts(datasets_dir: str | Path, verdict_path: str | Path, *,
                   write: bool = False) -> tuple[dict, list[ApplyReport]]:
    """Apply a verdict file to the datasets. Returns ``(payload, reports)``."""
    payload = json.loads(Path(verdict_path).read_text(encoding="utf-8"))
    problems = validate_verdicts(payload)
    if problems:
        raise ValueError("file dei verdetti non valido: " + "; ".join(problems))

    reviewer = payload["reviewer"]
    reviewed_at = payload["reviewed_at"]
    reports: list[ApplyReport] = []

    for dataset_name, entries in payload["verdicts"].items():
        path = Path(datasets_dir) / dataset_name
        rep = ApplyReport(dataset=dataset_name)
        if not path.exists():
            rep.skipped.append(f"dataset non trovato: {path}")
            reports.append(rep)
            continue
        data = load_dataset(path)
        by_seq = {i.get("sequence_index"): i for i in (data.get("items") or [])}
        src_counts: Counter = Counter()
        ed_counts: Counter = Counter()

        for key, entry in entries.items():
            item = by_seq.get(int(key))
            if item is None:
                rep.skipped.append(f"sequence_index {key} assente")
                continue
            note = str(entry.get("note") or "").strip()
            if entry.get("source"):
                item["source_audit_status"] = entry["source"]
                item["source_audited_at"] = reviewed_at
                item["source_audit_note"] = (
                    f"revisione manuale di {reviewer}"
                    + (f": {note}" if note else ""))
                src_counts[entry["source"]] += 1
            if entry.get("editorial"):
                item["editorial_status"] = entry["editorial"]
                item["editorial_note"] = (
                    f"revisione manuale di {reviewer} il {reviewed_at}"
                    + (f": {note}" if note else ""))
                ed_counts[entry["editorial"]] += 1
            rep.applied += 1

        rep.by_source_verdict = dict(src_counts)
        rep.by_editorial_verdict = dict(ed_counts)
        if write:
            save_dataset(path, data)
        reports.append(rep)
    return payload, reports
