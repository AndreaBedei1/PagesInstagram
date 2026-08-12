"""Apply the reviewed source replacements from ``source_fix_decisions.py``.

Writes the new URL, resets the item's audit state to ``not_checked`` (so the
next ``audit-sources`` run actually verifies the replacement instead of
inheriting the old verdict) and records why it changed. Items with no
replacement are left exactly as they are — broken, blocked and visible.

Usage::

    python tools/apply_source_fixes.py           # preview
    python tools/apply_source_fixes.py --write
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from source_fix_decisions import DECISIONS, WEAK, w  # noqa: E402

from src.content.dataset_io import load_dataset, save_dataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    grand = {"fixed": 0, "no_source": 0, "weak": 0, "unmatched": 0}
    for name, (fixed, no_source) in DECISIONS.items():
        path = ROOT / "datasets" / name
        data = load_dataset(path)
        ctype = data.get("content_type") or ""
        weak = WEAK.get(ctype, set())
        counts = {"fixed": 0, "no_source": 0, "weak": 0, "unmatched": 0}

        for item in data.get("items") or []:
            if (item.get("source_audit_status") or "") != "broken_source":
                continue
            seq = item.get("sequence_index")
            if seq in fixed:
                item["source_url"] = w(fixed[seq])
                item["source_audit_status"] = "not_checked"
                item["source_audited_at"] = None
                note = "URL sostituito dopo revisione manuale del link rotto"
                if seq in weak:
                    note += "; la voce copre l'argomento ma non l'affermazione specifica"
                    counts["weak"] += 1
                item["source_audit_note"] = note
                counts["fixed"] += 1
            elif seq in no_source:
                item["source_audit_note"] = f"nessuna fonte adeguata: {no_source[seq]}"
                counts["no_source"] += 1
            else:
                counts["unmatched"] += 1

        print(f"{name:32s} sostituiti={counts['fixed']:3d} "
              f"(di cui deboli={counts['weak']:2d}) "
              f"senza_fonte={counts['no_source']:3d} "
              f"non_decisi={counts['unmatched']:3d}")
        for k in grand:
            grand[k] += counts[k]
        if args.write:
            save_dataset(path, data)

    print(f"\ntotale: {grand}")
    if not args.write:
        print("(anteprima — usa --write per applicare)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
