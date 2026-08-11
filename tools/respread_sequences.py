"""Re-assign ``sequence_index`` so consecutive days do not share a category.

The datasets were authored category block by category block, and the sequence
index followed the authoring order. `world_curiosities` published 31 consecutive
days of the same category and 54 consecutive days of the same mood; a reader
would see a month of geography, then a month of biology. Nothing was duplicated
— the *ordering* was the problem.

The fix is a deterministic interleave: repeatedly take from the category with
the most items left, never twice in a row when an alternative exists, and among
equal candidates prefer the one whose mood differs from the previous day.
Indexes are then renumbered 0..N-1 with no gaps.

Determinism is preserved end to end — the algorithm has no randomness and the
result is a pure function of the input file — but the mapping from date to
content **changes**: after this runs, 2026-08-05 no longer resolves to the same
item it did before. That is a one-off, done before any real publication.

``today_in_history`` is left alone: its publication order is the calendar, and
its ``sequence_index`` only breaks ties between events sharing a date.

Usage::

    python tools/respread_sequences.py            # preview
    python tools/respread_sequences.py --write
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.content.dataset_io import load_dataset, save_dataset  # noqa: E402
from src.content.editorial_stats import _longest_run  # noqa: E402

#: Cyclic datasets only. today_in_history publishes by calendar, not by index.
DATASETS = ("philosophical_thoughts_it.json", "world_curiosities_it.json",
            "words_of_the_day_it.json", "daily_questions_it.json")


def interleave(items: list[dict]) -> list[dict]:
    """Spread items so neighbours differ in category, and in mood where possible.

    Category is the primary constraint: it is what a reader notices as "another
    week of geography". Each step takes from the largest remaining category that
    is not the previous day's — largest-first is what keeps a dominant category
    from piling up at the end once the small ones run out — and then, *within*
    that category, prefers an item whose mood differs from the previous day.

    The mood pass only works because mood now varies inside a category. When
    every ``scienza`` item carried ``focused``, three categories covering 46% of
    the dataset shared one mood and no ordering could break the run; the fix
    there was the mood pools, not the ordering.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in sorted(items, key=lambda i: i.get("sequence_index") or 0):
        buckets[str(item.get("category") or "—")].append(item)

    out: list[dict] = []
    last_cat: str | None = None
    last_mood: str | None = None

    while any(buckets.values()):
        live = sorted((c for c, b in buckets.items() if b),
                      key=lambda c: (-len(buckets[c]), c))
        pick = next((c for c in live if c != last_cat), live[0])

        bucket = buckets[pick]
        pos = next((n for n, it in enumerate(bucket)
                    if (it.get("mood") or "") != last_mood), 0)
        item = bucket.pop(pos)
        out.append(item)
        last_cat = pick
        last_mood = item.get("mood") or ""
    return out


def process(path: Path, *, write: bool) -> dict:
    data = load_dataset(path)
    items = data.get("items") or []
    before_cat = _longest_run([i.get("category") for i in
                               sorted(items, key=lambda x: x.get("sequence_index") or 0)])
    before_mood = _longest_run([i.get("mood") for i in
                                sorted(items, key=lambda x: x.get("sequence_index") or 0)])

    ordered = interleave(items)
    for n, item in enumerate(ordered):
        item["sequence_index"] = n
    data["items"] = ordered

    after_cat = _longest_run([i.get("category") for i in ordered])
    after_mood = _longest_run([i.get("mood") for i in ordered])
    if write:
        save_dataset(path, data)
    return {"dataset": path.name, "items": len(items),
            "cat": (before_cat, after_cat), "mood": (before_mood, after_mood)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    for name in DATASETS:
        path = ROOT / "datasets" / name
        if not path.exists():
            print(f"  MANCANTE {name}")
            continue
        s = process(path, write=args.write)
        print(f"{s['dataset']:34s} elementi={s['items']:4d} "
              f"run categoria {s['cat'][0]:3d} -> {s['cat'][1]:2d}   "
              f"run mood {s['mood'][0]:3d} -> {s['mood'][1]:2d}")
    if not args.write:
        print("\n(anteprima — usa --write per applicare)")
    else:
        print("\nRicorda: le CTA e i prompt seguono sequence_index, quindi "
              "rilancia tools/refresh_editorial_fields.py --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
