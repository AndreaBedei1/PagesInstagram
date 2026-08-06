"""Warn before the pinned Graph API version stops being supported.

Meta gives each version roughly two years, and the failure mode is silent: the
calls keep working until the day they do not. This reads the official changelog,
finds the row for the version this project pins, and fails when its end of
support is closer than the warning window.

It deliberately does not bump anything. Moving to a new version means
re-verifying the resumable flow, the Reel specifications and the permissions
against that version's documentation — a decision, not a dependency update.

Two things it has to survive, because both were seen while writing it:

* the changelog is served localised, so "July 29, 2028" arrives as
  "29 luglio 2028" depending on where the request comes from;
* the rows sit close together, so taking the first two dates after the version
  string picks up the neighbouring row's expiry. A pair is accepted only when
  the second date is after the first.

A page it cannot read is reported and exits 0: a weekly job that fails on a
layout change trains people to ignore it.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from src.core.meta_api import (DEFAULT_GRAPH_API_VERSION,  # noqa: E402
                               GRAPH_API_VERIFIED_AT, LATEST_GRAPH_API_VERSION)

CHANGELOG = "https://developers.facebook.com/docs/graph-api/changelog"
UA = "InstagramContentEngine-MetaWatch/1.0 (documentation check)"

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "novembre": 11, "dicembre": 12,
}

_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
#: "February 18, 2026" and "18 febbraio 2026" both.
DATE_RE = re.compile(
    rf"(?:(?P<m1>{_NAMES})\s+(?P<d1>\d{{1,2}}),?\s*(?P<y1>\d{{4}}))"
    rf"|(?:(?P<d2>\d{{1,2}})\s+(?P<m2>{_NAMES})\s+(?P<y2>\d{{4}}))",
    re.IGNORECASE)


def dates_in(text: str) -> list[date]:
    out: list[date] = []
    for m in DATE_RE.finditer(text):
        month = (m.group("m1") or m.group("m2") or "").lower()
        day = m.group("d1") or m.group("d2")
        year = m.group("y1") or m.group("y2")
        if month in MONTHS and day and year:
            try:
                out.append(date(int(year), MONTHS[month], int(day)))
            except ValueError:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warn-days", type=int, default=180)
    args = ap.parse_args()

    print(f"versione fissata dal progetto : {DEFAULT_GRAPH_API_VERSION}")
    print(f"ultima nota al {GRAPH_API_VERIFIED_AT}     : {LATEST_GRAPH_API_VERSION}")

    try:
        html = requests.get(CHANGELOG, timeout=40,
                            headers={"User-Agent": UA}).text
    except requests.RequestException as e:
        print(f"changelog non raggiungibile ({e}): riprovo la settimana prossima")
        return 0

    text = re.sub(r"<[^>]+>", " ", html)
    start = text.find(DEFAULT_GRAPH_API_VERSION)
    if start < 0:
        print(f"{DEFAULT_GRAPH_API_VERSION} non compare nel changelog: "
              f"controlla a mano {CHANGELOG}")
        return 0

    found = dates_in(text[start:start + 600])
    pair = next(((a, b) for a, b in zip(found, found[1:]) if b > a), None)
    if pair is None:
        print(f"riga di {DEFAULT_GRAPH_API_VERSION} non interpretabile "
              f"(layout cambiato?): controlla a mano {CHANGELOG}")
        return 0

    released, expires = pair
    remaining = (expires - date.today()).days
    print(f"rilascio      : {released}")
    print(f"scadenza      : {expires}")
    print(f"giorni residui: {remaining}")

    if remaining < args.warn_days:
        print(f"\nLa versione {DEFAULT_GRAPH_API_VERSION} scade fra meno di "
              f"{args.warn_days} giorni.")
        print("Aggiornare significa riverificare il flusso resumable, le "
              "specifiche dei Reel e i permessi sulla documentazione ufficiale "
              "della nuova versione, poi aggiornare src/core/meta_api.py.")
        return 1

    print("\nversione ancora ampiamente supportata")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
