"""Propose replacement URLs for the source links the audit found broken.

The broken links are all auto-generated Wikipedia titles that were never
checked: ``Diffusione_di_Rayleigh`` instead of ``Scattering_di_Rayleigh``,
``Petra_(sito_archeologico)`` instead of ``Petra_(Giordania)``, and so on. The
*facts* are usually fine; the URL was invented from the subject line.

This tool only **proposes**. For each broken URL it asks the official MediaWiki
search API for existing articles matching the subject, and writes the candidates
to ``reports/source_fix_proposals.json`` next to the claim they are supposed to
support. A human then reads the claim, picks the article that actually covers
it, and records the decision — nothing here writes a dataset.

Usage::

    python tools/propose_source_fixes.py                 # all broken links
    python tools/propose_source_fixes.py --dataset world_curiosities_it.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.content.dataset_io import load_dataset  # noqa: E402
from src.content.source_audit import USER_AGENT  # noqa: E402

API = "https://it.wikipedia.org/w/api.php"
DATASETS = ("world_curiosities_it.json", "today_in_history_it.json",
            "words_of_the_day_it.json")


def search(session: requests.Session, query: str, limit: int = 5) -> list[dict]:
    try:
        r = session.get(API, timeout=25, headers={"User-Agent": USER_AGENT},
                        params={"action": "query", "format": "json",
                                "list": "search", "srsearch": query,
                                "srlimit": limit, "srprop": "snippet"})
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        return [{"error": str(e)[:120]}]
    out = []
    for hit in (data.get("query") or {}).get("search") or []:
        title = hit.get("title") or ""
        out.append({
            "title": title,
            "url": "https://it.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "snippet": (hit.get("snippet") or "").replace('<span class="searchmatch">', "")
                                                 .replace("</span>", ""),
        })
    return out


def subject_from_url(url: str) -> str:
    path = urlparse(url).path
    title = unquote(path.rsplit("/", 1)[-1]).replace("_", " ")
    # Drop a disambiguator such as "(sito archeologico)" for the search query.
    if "(" in title:
        title = title.split("(")[0].strip()
    return title


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", action="append", default=None)
    ap.add_argument("--rate", type=float, default=3.0, help="richieste al secondo")
    ap.add_argument("--out", default="reports/source_fix_proposals.json")
    args = ap.parse_args()

    names = args.dataset or list(DATASETS)
    session = requests.Session()
    interval = 1.0 / max(args.rate, 0.2)

    proposals: list[dict] = []
    for name in names:
        path = ROOT / "datasets" / name
        if not path.exists():
            print(f"  MANCANTE {name}")
            continue
        data = load_dataset(path)
        broken = [it for it in (data.get("items") or [])
                  if (it.get("source_audit_status") or "") == "broken_source"]
        print(f"{name}: {len(broken)} fonti rotte")
        for n, item in enumerate(broken, 1):
            subject = subject_from_url(item.get("source_url") or "")
            candidates = search(session, subject)
            proposals.append({
                "dataset": name,
                "sequence_index": item.get("sequence_index"),
                "id": item.get("id"),
                "claim": item.get("text"),
                "explanation": item.get("explanation"),
                "calendar_key": item.get("calendar_key"),
                "metadata": item.get("metadata"),
                "broken_url": item.get("source_url"),
                "search_query": subject,
                "candidates": candidates,
            })
            if n % 20 == 0:
                print(f"   {n}/{len(broken)}")
            time.sleep(interval)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proposals, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n{len(proposals)} proposte -> {out}")
    print("Nessun dataset è stato modificato: le proposte vanno riviste a mano.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
