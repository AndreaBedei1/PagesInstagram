"""Authored content parts. Each ``part_NN.py`` exports ``BATCH``."""
from __future__ import annotations

import importlib
import pkgutil

WORDS: list[tuple] = []

for _mod in sorted(m.name for m in pkgutil.iter_modules(__path__)
                   if m.name.startswith("part_")):
    WORDS.extend(importlib.import_module(f"{__name__}.{_mod}").BATCH)
