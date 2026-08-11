"""Rewrite the rotating editorial fields of the existing datasets, in place.

The datasets are no longer pure build artifacts: they carry source-audit
verdicts, editorial sign-offs and hand-applied corrections. Regenerating them
from ``build_datasets.py`` would erase all of that, so the call-to-action,
background-prompt and mood pools are re-applied here instead, touching **only**
those three fields and leaving every other key exactly as it is.

Run::

    python tools/refresh_editorial_fields.py            # show what would change
    python tools/refresh_editorial_fields.py --write    # apply

Deterministic: the same ``sequence_index`` always maps to the same entry, so
running it twice changes nothing the second time.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from dataset_build.editorial_pools import (EXTRA_TAGS, cta_for,  # noqa: E402
                                           hashtags_for, mood_for, prompt_for,
                                           rotation_index)
from src.content.dataset_io import load_dataset, save_dataset  # noqa: E402

DATASETS = (
    "philosophical_thoughts_it.json",
    "world_curiosities_it.json",
    "words_of_the_day_it.json",
    "today_in_history_it.json",
    "daily_questions_it.json",
)


def refresh(path: Path, *, write: bool) -> dict:
    data = load_dataset(path)
    ctype = data.get("content_type") or ""
    items = data.get("items") or []
    changed = Counter()

    pool = set(EXTRA_TAGS.get(ctype, ()))
    for item in items:
        if not isinstance(item.get("sequence_index"), int):
            continue
        # Not sequence_index: the position a reader sees this item at. For
        # today_in_history that is the day of the year, not the index.
        idx = rotation_index(ctype, item)
        new_cta = cta_for(ctype, idx)
        if item.get("call_to_action") != new_cta:
            changed["call_to_action"] += 1
        # An empty call to action is stored as null, not as an empty string, so
        # the renderer's "is this block present?" check keeps working.
        item["call_to_action"] = new_cta or None

        new_prompt = prompt_for(ctype, idx)
        if new_prompt and item.get("background_prompt") != new_prompt:
            changed["background_prompt"] += 1
            item["background_prompt"] = new_prompt

        new_mood = mood_for(ctype, idx, item.get("mood") or "reflective",
                            category=item.get("category"))
        if new_mood != item.get("mood"):
            changed["mood"] += 1
            item["mood"] = new_mood

        # The item's own topic tags stay (they describe the content); only the
        # rotating page tags are re-dealt against the new position.
        topic = [t for t in (item.get("hashtags") or []) if t not in pool]
        new_tags = hashtags_for(ctype, idx, topic)
        if new_tags != (item.get("hashtags") or []):
            changed["hashtags"] += 1
            item["hashtags"] = new_tags

    stats = {
        "dataset": path.name,
        "items": len(items),
        "changed": dict(changed),
        "distinct_cta": len({i.get("call_to_action") for i in items}),
        "distinct_prompt": len({i.get("background_prompt") for i in items}),
        "distinct_mood": len({i.get("mood") for i in items}),
        "distinct_tagsets": len({tuple(i.get("hashtags") or []) for i in items}),
    }
    if write:
        save_dataset(path, data)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="apply the changes")
    args = ap.parse_args()

    for name in DATASETS:
        path = ROOT / "datasets" / name
        if not path.exists():
            print(f"  MANCANTE {name}")
            continue
        s = refresh(path, write=args.write)
        print(f"{s['dataset']:34s} elementi={s['items']:4d} "
              f"cta={s['distinct_cta']:3d} prompt={s['distinct_prompt']:3d} "
              f"mood={s['distinct_mood']:2d} tagset={s['distinct_tagsets']:3d} "
              f"modifiche={s['changed']}")
    if not args.write:
        print("\n(anteprima — usa --write per applicare)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
