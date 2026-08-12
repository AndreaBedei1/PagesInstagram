"""Read and write the dataset JSON files without reformatting them.

Several tools now edit datasets in place — the source audit, the editorial
review, the language fixes. If each one picked its own ``json.dumps`` options,
the first write would reflow all 1.000 items and bury a two-line change in a
40.000-line diff.

One writer, matching what ``tools/dataset_build`` produces: ``indent=1``,
``ensure_ascii=False``, trailing newline. Reviewing a dataset change stays
possible.
"""
from __future__ import annotations

import json
from pathlib import Path

INDENT = 1


def load_dataset(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_dataset(path: str | Path, data: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=INDENT) + "\n",
                 encoding="utf-8")
    return p


def dataset_items(path: str | Path) -> list[dict]:
    return load_dataset(path).get("items") or []
