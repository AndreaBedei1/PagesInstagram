"""Pensiero Essenziale — authored thoughts, split into review-sized parts.

Each ``part_NN.py`` exports ``BATCH``: a list of
``(text, expansion, category, mood)`` tuples.

* ``text``      — what appears on the image. Original, unattributed, <= ~25 words.
* ``expansion`` — the caption: a different sentence that develops the thought.
* ``category``  — a key of ``CATEGORY_PROFILES`` in the builder module.
* ``mood``      — a value from ``src.core.enums.MOODS``.
"""
from __future__ import annotations

import importlib
import pkgutil

THOUGHTS: list[tuple[str, str, str, str]] = []

for _mod in sorted(m.name for m in pkgutil.iter_modules(__path__)
                   if m.name.startswith("part_")):
    THOUGHTS.extend(importlib.import_module(f"{__name__}.{_mod}").BATCH)
