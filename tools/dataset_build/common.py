"""Shared assembly helpers for the dataset builders.

The *content* (thought, question, lemma, fact, event) is authored by hand in the
sibling modules. This module only assembles the surrounding, non-editorial
metadata — hashtags, background prompts, sequence indexes, the JSON envelope —
in a deterministic way, so regenerating a dataset never reshuffles it.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1
VERIFIED_AT = "2026-08-04"     # date the sources were checked for this release

DATASETS_DIR = Path(__file__).resolve().parents[2] / "datasets"


def slugify(value: str) -> str:
    """ASCII slug used for stable ids (``Pensiero essenziale`` → ``pensiero-essenziale``)."""
    norm = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(c for c in norm if not unicodedata.combining(c))
    out = []
    for ch in ascii_only.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:60] or "item"


def pick(pool: list, index: int):
    """Deterministic rotation through a pool (never random)."""
    return pool[index % len(pool)]


def rotate(pool: list, index: int, n: int) -> list:
    """``n`` consecutive elements of ``pool`` starting at ``index`` (wrapping)."""
    if not pool:
        return []
    start = index % len(pool)
    doubled = pool + pool
    return doubled[start:start + min(n, len(pool))]


def hashtags(base: list[str], extra_pool: list[str], index: int,
             total: int = 6) -> list[str]:
    """``base`` tags plus a rotating slice of ``extra_pool``, de-duplicated."""
    out: list[str] = []
    for tag in base + rotate(extra_pool, index, max(0, total - len(base))):
        if tag not in out:
            out.append(tag)
    return out[:total]


def treccani_vocabolario(lemma: str) -> str:
    """Canonical Treccani ``vocabolario`` entry URL for an Italian lemma."""
    return f"https://www.treccani.it/vocabolario/{slugify(lemma)}/"


def wikipedia_it(title: str) -> str:
    """Canonical Italian Wikipedia article URL for a title."""
    return "https://it.wikipedia.org/wiki/" + title.replace(" ", "_")


def treccani_enciclopedia(slug: str) -> str:
    return f"https://www.treccani.it/enciclopedia/{slug}/"


def write_dataset(filename: str, content_type: str, items: list[dict], *,
                  language: str = "it", notes: str = "") -> Path:
    """Write ``datasets/<filename>`` with a stable, diff-friendly layout."""
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "content_type": content_type,
        "language": language,
        "generated_by": "tools/build_datasets.py",
        "notes": notes,
        "items": items,
    }
    path = DATASETS_DIR / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def assert_unique(values: list, label: str) -> None:
    seen: dict = {}
    for i, v in enumerate(values):
        if v in seen:
            raise SystemExit(f"{label}: valore duplicato {v!r} (indici {seen[v]} e {i})")
        seen[v] = i
