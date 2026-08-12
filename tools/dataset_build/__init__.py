"""Authoring sources for the five evergreen datasets.

The committed ``datasets/*.json`` files are *generated* from the modules in this
package by ``tools/build_datasets.py``. Keeping the authored text in compact
Python tuples (instead of hand-editing 1.000-object JSON files) makes review and
incremental additions practical, while the JSON stays the single runtime format.

Regenerate everything with::

    python tools/build_datasets.py
    python -m src.cli validate-datasets
"""
