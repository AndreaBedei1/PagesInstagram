"""Consistency checks for the ``today_in_history`` dataset.

An event published on the wrong day is the one failure this page cannot
survive, and it is invisible to every generic check: the text is fine, the
source resolves, the date is a valid ``MM-DD``. What matters is whether the
date, the year, the title and the description agree with each other.

Checks:

* ``calendar_key`` is a real ``MM-DD``, 29 February included;
* the year is present, numeric, plausible, and not in the future;
* a date written out in the text ("19 febbraio") matches ``calendar_key``;
* a year written out in the text matches ``metadata.year``;
* 29 February events are flagged for review, because a date that exists once
  every four years is a common place to file an event by mistake;
* pre-Gregorian events are flagged: the project's convention has to be stated
  rather than left implicit (see docs/DATASET_SOURCES.md).

The calendar convention is **not** decided here — the dataset records the date
as modern sources commonly report it, and events before the local adoption of
the Gregorian calendar carry ``metadata.calendar_note`` when the two differ.
This module's job is to make an undeclared discrepancy visible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls

MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
          "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")
MONTH_NUMBER = {name: i + 1 for i, name in enumerate(MONTHS)}
DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

#: Gregorian calendar introduced 1582-10-15 in the Catholic countries; adoption
#: elsewhere ran to 1923. Events before this need an explicit convention.
GREGORIAN_YEAR = 1582
#: Last year of adoption anywhere (Greece, 1923) — the grey zone ends here.
ADOPTION_END_YEAR = 1923

_DATE_IN_TEXT = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\b", re.IGNORECASE)
_YEAR_IN_TEXT = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b")
#: Words that turn the number that follows into a title, not a date.
_TITLE_BEFORE = re.compile(
    r"\b(romanzo|libro|film|album|opera|rivista|canzone|brano|titolo)\s+\S*$",
    re.IGNORECASE)

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class HistoryIssue:
    rule: str
    severity: str
    message: str
    excerpt: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "excerpt": self.excerpt}


def parse_calendar_key(key: str | None) -> tuple[int, int] | None:
    if not key or not isinstance(key, str) or len(key) != 5 or key[2] != "-":
        return None
    try:
        month, day = int(key[:2]), int(key[3:])
    except ValueError:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= DAYS_IN_MONTH[month - 1]:
        return None
    return month, day


def check_event(item: dict, *, today: date_cls | None = None) -> list[HistoryIssue]:
    """Every inconsistency found in one historical event."""
    out: list[HistoryIssue] = []
    today = today or date_cls.today()

    key = item.get("calendar_key")
    parsed = parse_calendar_key(key)
    if parsed is None:
        out.append(HistoryIssue("calendar_key", ERROR,
                                f"calendar_key non valido ({key!r})"))
        return out
    month, day = parsed

    meta = item.get("metadata") or {}
    raw_year = str(meta.get("year") or "").strip()
    year: int | None = None
    if not raw_year:
        out.append(HistoryIssue("anno_mancante", ERROR, "metadata.year assente"))
    else:
        try:
            year = int(raw_year)
        except ValueError:
            out.append(HistoryIssue("anno_non_numerico", ERROR,
                                    f"metadata.year non numerico ({raw_year!r})"))
        else:
            if year > today.year:
                out.append(HistoryIssue(
                    "evento_futuro", ERROR,
                    f"anno {year} nel futuro (oggi è {today.year})"))
            elif not -3000 <= year <= today.year:
                out.append(HistoryIssue("anno_implausibile", ERROR,
                                        f"anno {year} implausibile"))

    headline = str(item.get("text") or "")
    body = " ".join(str(item.get(f) or "") for f in ("caption", "explanation"))
    text = f"{headline} {body}"

    # A declared convention is not a discrepancy. The February Revolution is
    # filed on 8 March and says so: "la data corrisponde al 23 febbraio del
    # calendario allora in uso in Russia".
    declared = bool(str(meta.get("calendar_note") or "").strip())

    for field_name, field_text, severity in (
            ("text", headline, ERROR), ("descrizione", body, WARNING)):
        for m in _DATE_IN_TEXT.finditer(field_text):
            t_day, t_month = int(m.group(1)), MONTH_NUMBER[m.group(2).lower()]
            if t_day > DAYS_IN_MONTH[t_month - 1]:
                out.append(HistoryIssue("data_inesistente", ERROR,
                                        f"«{m.group(0)}» non esiste", m.group(0)))
            elif (t_month, t_day) != (month, day) and not declared:
                out.append(HistoryIssue(
                    "data_discordante", severity,
                    f"{field_name}: «{m.group(0)}» non coincide con "
                    f"calendar_key {key} e non c'è metadata.calendar_note",
                    m.group(0)))

    # Only the headline. The description is *supposed* to mention other years —
    # "Plutone, riclassificato nel 2006", "il Patto di Varsavia, sciolto nel
    # 1991" — and flagging those produced 25 findings and zero defects.
    if year is not None:
        # A four-digit number in a headline is not always a year: "Pubblicato il
        # romanzo 1984 di George Orwell" is a title.
        years_in_headline = {
            int(m.group(1)) for m in _YEAR_IN_TEXT.finditer(headline)
            if not _TITLE_BEFORE.search(headline[max(0, m.start() - 24):m.start()])
        }
        if years_in_headline and year not in years_in_headline:
            out.append(HistoryIssue(
                "anno_discordante", ERROR,
                f"il titolo cita {sorted(years_in_headline)} ma metadata.year "
                f"è {year}",
                ", ".join(str(y) for y in sorted(years_in_headline))))

    if (month, day) == (2, 29):
        out.append(HistoryIssue(
            "ventinove_febbraio", WARNING,
            "evento al 29 febbraio: verificare che la data sia davvero quella"))

    if year is not None and year < GREGORIAN_YEAR:
        if not str(meta.get("calendar_note") or "").strip():
            out.append(HistoryIssue(
                "calendario_giuliano", WARNING,
                f"evento del {year}, precedente alla riforma gregoriana: "
                f"serve metadata.calendar_note che dichiari la convenzione"))
    elif year is not None and year < ADOPTION_END_YEAR:
        out.append(HistoryIssue(
            "adozione_gregoriana", WARNING,
            f"evento del {year}: alcuni paesi non avevano ancora adottato il "
            f"calendario gregoriano, controllare la convenzione della fonte"))
    return out


def check_dataset(items: list[dict], *,
                  today: date_cls | None = None) -> dict[int, list[HistoryIssue]]:
    found: dict[int, list[HistoryIssue]] = {}
    for i, item in enumerate(items):
        issues = check_event(item, today=today)
        if issues:
            found[i] = issues
    return found


def calendar_coverage(items: list[dict]) -> dict[str, int]:
    """``MM-DD`` -> number of events. Every one of the 366 keys must be present."""
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("calendar_key") or "")
        if parse_calendar_key(key):
            counts[key] = counts.get(key, 0) + 1
    return counts
