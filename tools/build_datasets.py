#!/usr/bin/env python
"""Regenerate the five evergreen datasets from the authored sources.

    python tools/build_datasets.py            # build all five
    python tools/build_datasets.py words      # build one

Then always run::

    python -m src.cli validate-datasets

The JSON under ``datasets/`` is the runtime format and is committed; the Python
sources under ``tools/dataset_build/`` are the editable originals.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dataset_build import (daily_questions, philosophical_thoughts,  # noqa: E402
                                 today_in_history, words_of_the_day,
                                 world_curiosities)
from tools.dataset_build.common import assert_unique, write_dataset  # noqa: E402

BUILDERS = {
    "thoughts": (philosophical_thoughts, "philosophical_thoughts_it.json",
                 "Pensieri filosofici originali, senza attribuzione."),
    "curiosities": (world_curiosities, "world_curiosities_it.json",
                    "Curiosità verificate con fonte, nome fonte, URL e data di verifica."),
    "words": (words_of_the_day, "words_of_the_day_it.json",
              "Lemmi italiani con definizione riscritta, esempio originale e fonte lessicografica."),
    "history": (today_in_history, "today_in_history_it.json",
                "Eventi storici con calendar_key MM-DD, fonte e data di verifica."),
    "questions": (daily_questions, "daily_questions_it.json",
                  "Domande originali, non intrusive, tematicamente alternate."),
}


def build_one(key: str) -> tuple[Path, int]:
    module, filename, notes = BUILDERS[key]
    items = module.build()
    assert_unique([it["text"] for it in items], f"{key}: text")
    if items and "sequence_index" in items[0]:
        assert_unique([it["sequence_index"] for it in items], f"{key}: sequence_index")
    path = write_dataset(filename, module.CONTENT_TYPE, items, notes=notes)
    return path, len(items)


def main(argv: list[str]) -> int:
    keys = argv[1:] or list(BUILDERS)
    unknown = [k for k in keys if k not in BUILDERS]
    if unknown:
        print(f"dataset sconosciuto: {unknown}. Disponibili: {list(BUILDERS)}")
        return 2
    total = 0
    for key in keys:
        path, n = build_one(key)
        total += n
        print(f"{path.name:34s} {n:5d} elementi")
    print(f"{'TOTALE':34s} {total:5d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
