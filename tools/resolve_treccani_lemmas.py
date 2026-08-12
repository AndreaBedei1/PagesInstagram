"""Find the real Treccani entry for the lemmas whose generated URL is a soft 404.

The dataset built every source link as ``treccani.it/vocabolario/<slug>``. That
is right for most lemmas and wrong for three families, and Treccani hides the
difference: a URL it does not have answers **200** and redirects to the
homepage, so the first audit scored 75 dead links as "reachable, just moved".

The three families:

* homographs carry a numeric suffix — ``scempio`` is at ``scempio1``,
  ``rombo`` at ``rombo1``;
* pronominal verbs live under the base form — ``assopirsi`` at ``assopire``,
  ``affrancarsi`` at ``affrancare``;
* a few lemmas simply differ — ``abbrivio`` is filed as ``abbrivo``.

For each broken lemma this tries an ordered list of candidates and **asks the
server** about each one, keeping the first that returns a real path. Nothing is
guessed and left unverified: a lemma with no working candidate is reported so a
person can replace the word or the source.

Usage::

    python tools/resolve_treccani_lemmas.py            # probe and report
    python tools/resolve_treccani_lemmas.py --write    # write the URLs found
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.content.dataset_io import load_dataset, save_dataset  # noqa: E402
from src.content.source_audit import USER_AGENT, _redirects_to_root  # noqa: E402

BASE = "https://www.treccani.it/vocabolario/{}/"

#: Lemmas whose Treccani form differs in a way no rule predicts. Each was
#: looked up by hand and confirmed against the live entry.
MANUAL: dict[str, str] = {
    "abbrivio": "abbrivo",       # Treccani files the noun as "abbrivo"
    "invalso": "invalere",       # past participle, entry under the verb
    "distoglere": "distogliere",  # the dataset spelling was wrong
}

#: Participle-shaped adjectives are filed under their verb.
_PARTICIPLE_ENDINGS = (
    ("ante", "are"), ("ente", "ere"), ("ente", "ire"),
    ("ato", "are"), ("ito", "ire"), ("uto", "ere"),
)


def candidates(lemma: str, part_of_speech: str) -> list[str]:
    """Slugs worth trying, most likely first."""
    lemma = lemma.strip().lower()
    out = [lemma]
    if lemma in MANUAL:
        out.insert(0, MANUAL[lemma])

    # Pronominal verbs are filed under the base form.
    for suffix, base in (("arsi", "are"), ("ersi", "ere"), ("irsi", "ire")):
        if lemma.endswith(suffix):
            out.append(lemma[: -len(suffix)] + base)

    # Participles used as adjectives are filed under the verb.
    if "aggettivo" in part_of_speech or "participio" in part_of_speech:
        for suffix, base in _PARTICIPLE_ENDINGS:
            if lemma.endswith(suffix):
                out.append(lemma[: -len(suffix)] + base)

    # Adverbs in -mente are usually under the adjective.
    if lemma.endswith("mente") and "avverbio" in part_of_speech:
        stem = lemma[:-5]
        out.append(stem)
        if stem.endswith("a"):
            out.append(stem[:-1] + "o")

    # Homographs.
    seeds = list(out)
    for seed in seeds:
        out.extend(f"{seed}{n}" for n in (1, 2, 3))

    seen: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.append(c)
    return seen


def probe(session: requests.Session, slug: str, *, timeout: float = 20.0) -> str | None:
    """Return the URL if this slug is a real entry, else ``None``."""
    url = BASE.format(slug)
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True,
                        headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return None
    if r.status_code != 200 or _redirects_to_root(url, r.url):
        return None
    return r.url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--rate", type=float, default=3.0)
    args = ap.parse_args()

    path = ROOT / "datasets" / "words_of_the_day_it.json"
    data = load_dataset(path)
    broken = [i for i in (data.get("items") or [])
              if (i.get("source_audit_status") or "") == "broken_source"]
    print(f"{len(broken)} lemmi da risolvere\n")

    session = requests.Session()
    interval = 1.0 / max(args.rate, 0.2)
    resolved, unresolved = 0, []

    for item in broken:
        lemma = item.get("text") or ""
        pos = str((item.get("metadata") or {}).get("part_of_speech") or "")
        found = None
        for slug in candidates(lemma, pos):
            found = probe(session, slug)
            time.sleep(interval)
            if found:
                break
        if found:
            print(f"  OK   {lemma:20s} -> {found[38:]}")
            item["source_url"] = found
            item["source_audit_status"] = "not_checked"
            item["source_audited_at"] = None
            item["source_audit_note"] = "slug Treccani corretto dopo verifica live"
            resolved += 1
        else:
            print(f"  --   {lemma:20s} nessuna voce trovata")
            unresolved.append(lemma)

    print(f"\nrisolti {resolved}/{len(broken)}; senza voce: {len(unresolved)}")
    if unresolved:
        print("  " + ", ".join(unresolved))
    if args.write:
        save_dataset(path, data)
        print("dataset aggiornato")
    else:
        print("(anteprima — usa --write per applicare)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
