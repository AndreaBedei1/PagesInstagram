"""Simulate the whole publication cycle before a single real post is made.

One thousand days times five pages is five thousand selections. This runs every
one of them through the real machinery — the real page configs, the real
importer, the real selection policies, the real production gate — against a
database built from nothing, and reports what would actually happen on each of
those days.

What it is not: a structural check on the datasets. `corpus-final-gate` already
answers "is the corpus sound". This answers a different question — "does every
day of the cycle resolve to a publishable content, in the page's own timezone,
without collisions" — and the two can disagree. A corpus can be perfectly sound
and still leave 29 February uncovered, or hand the same content to two different
days because an anchor was mis-set.

Deliberately strict:

* the production gate is on, exactly as it would be in production;
* dates are resolved in Europe/Rome, so a day that summer time would shift is
  resolved where the reader is, not in UTC;
* the same content appearing twice on one page inside the cycle is a failure —
  with one exception, counted separately. A calendar page publishes an event on
  its anniversary, and a thousand days spans three occurrences of most MM-DD
  keys. Three occurrences cannot be filled from fewer than three distinct events,
  and 366 keys x 3 is 1.098 — more than the thousand items the page holds. A
  repeat on the same calendar key in a later year is therefore arithmetic, not a
  fault, and is reported as `anniversary_repeats`. A repeat on a *different* key,
  or twice in the same year, is a real duplicate and fails;
* a missing day is a failure even if every other day is fine.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..content.selection import SelectionError, select_for_date
from ..content.verification import is_publishable
from ..core.enums import Mode
from ..database import Database


@dataclass
class PageReadiness:
    page_id: str
    content_type: str = ""
    days: int = 0
    publishable: int = 0
    missing: int = 0
    not_ready: int = 0
    duplicates: int = 0
    anniversary_repeats: int = 0
    date_errors: int = 0
    distinct_contents: int = 0
    first_repeat_after_days: int | None = None
    problems: list = field(default_factory=list)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["problems"] = self.problems[:20]
        return data


@dataclass
class ReadinessReport:
    start: str
    days: int
    timezone: str = "Europe/Rome"
    pages: list = field(default_factory=list)
    contents_imported: int = 0

    @property
    def jobs_checked(self) -> int:
        return sum(p.days for p in self.pages)

    @property
    def production_ready(self) -> int:
        return sum(p.publishable for p in self.pages)

    @property
    def missing(self) -> int:
        return sum(p.missing for p in self.pages)

    @property
    def needs_review(self) -> int:
        return sum(p.not_ready for p in self.pages)

    @property
    def duplicates(self) -> int:
        return sum(p.duplicates for p in self.pages)

    @property
    def anniversary_repeats(self) -> int:
        return sum(p.anniversary_repeats for p in self.pages)

    @property
    def date_errors(self) -> int:
        return sum(p.date_errors for p in self.pages)

    @property
    def ok(self) -> bool:
        return (self.jobs_checked > 0
                and self.production_ready == self.jobs_checked
                and self.missing == 0 and self.needs_review == 0
                and self.duplicates == 0 and self.date_errors == 0)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "generated_at": datetime.now(timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days_checked": self.days,
            "pages_checked": len(self.pages),
            "jobs_checked": self.jobs_checked,
            "production_ready": self.production_ready,
            "needs_review": self.needs_review,
            "blocked": self.needs_review,
            "missing": self.missing,
            "duplicate_jobs": self.duplicates,
            "anniversary_repeats": self.anniversary_repeats,
            "date_errors": self.date_errors,
            "start_date": self.start,
            "timezone": self.timezone,
            "contents_imported": self.contents_imported,
            "pages": [p.as_dict() for p in self.pages],
        }


def _local_dates(start: date_cls, days: int, tz_name: str) -> list[str]:
    """The sequence of local dates a page publishes on.

    Built by walking the calendar rather than adding 24-hour steps to a UTC
    instant: on the two days a year when summer time changes, those are not the
    same thing, and the selection policy keys on the local date.
    """
    tz = ZoneInfo(tz_name)
    out: list[str] = []
    current = start
    for _ in range(days):
        # Touch the timezone so an invalid local date would raise here rather
        # than silently resolve to something else at publication time.
        datetime(current.year, current.month, current.day, 12, 0, tzinfo=tz)
        out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def run_readiness(settings, *, paths, start: str, days: int,
                  progress=None) -> ReadinessReport:
    from ..accounts.registry import load_pages
    from ..content.importer import import_dataset
    from ..database.repositories import Database as _Db  # noqa: F401

    start_date = date_cls.fromisoformat(start)
    report = ReadinessReport(start=start, days=days)

    workdir = Path(tempfile.mkdtemp(prefix="ice-readiness-"))
    try:
        db = Database.open(workdir / "readiness.sqlite", migrate=True)
        registry = load_pages(paths)
        pages = registry.enabled()

        for path in sorted(Path(paths.datasets).glob("*.json")):
            rep = import_dataset(db, path)
            report.contents_imported += rep.added
        if progress:
            progress(f"importati {report.contents_imported} contenuti")

        for page in pages:
            pr = PageReadiness(page_id=page.page_id,
                               content_type=page.content_type)
            seen: dict[int, str] = {}
            counts: Counter = Counter()
            try:
                dates = _local_dates(start_date, days,
                                     page.publishing.timezone)
            except Exception as e:                      # noqa: BLE001
                pr.date_errors += 1
                pr.problems.append(f"calendario non risolvibile: {e}")
                report.pages.append(pr)
                continue

            for iso in dates:
                pr.days += 1
                try:
                    sel = select_for_date(db, page, iso,
                                          require_production_ready=True)
                except SelectionError as e:
                    pr.missing += 1
                    pr.problems.append(f"{iso}: {e}")
                    continue

                content = sel.content
                ready = is_publishable(content, content_type=page.content_type)
                if not ready.ok:
                    pr.not_ready += 1
                    pr.problems.append(
                        f"{iso}: contenuto {content.get('id')} non pubblicabile "
                        f"({'; '.join(ready.reasons)[:80]})")
                    continue

                cid = int(content["id"])
                if cid in seen:
                    previous = date_cls.fromisoformat(seen[cid])
                    this_day = date_cls.fromisoformat(iso)
                    if pr.first_repeat_after_days is None:
                        pr.first_repeat_after_days = (this_day - previous).days
                    same_anniversary = (
                        page.content.selection_policy == "calendar_rotating"
                        and (previous.month, previous.day)
                        == (this_day.month, this_day.day))
                    if same_anniversary:
                        pr.anniversary_repeats += 1
                    else:
                        pr.duplicates += 1
                        pr.problems.append(
                            f"{iso}: contenuto {cid} già usato il {seen[cid]}")
                    seen[cid] = iso
                else:
                    seen[cid] = iso
                counts[cid] += 1
                pr.publishable += 1

            pr.distinct_contents = len(seen)
            report.pages.append(pr)
            if progress:
                progress(f"{page.page_id}: {pr.publishable}/{pr.days} pubblicabili")
        db.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return report


def write_json(report: ReadinessReport, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out


_CSS = """
body{font:15px/1.55 system-ui,Segoe UI,sans-serif;margin:0;background:#f6f7f9;color:#1c1f23}
main{max-width:1000px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 4px}.sub{color:#5a6371;margin:0 0 24px}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 2px #0001}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid #e6e9ee}
th{background:#eef1f5;font-weight:600}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}
.card{background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 2px #0001;min-width:140px}
.card b{display:block;font-size:25px}.card span{color:#5a6371;font-size:13px}
.ok{color:#1c6b33;font-weight:600}.bad{color:#992222;font-weight:600}
@media(prefers-color-scheme:dark){body{background:#15181c;color:#e6e9ee}
table,.card{background:#1e2228;box-shadow:none}th{background:#262b32}
th,td{border-bottom:1px solid #2c323a}}
"""


def write_html(report: ReadinessReport, out_path: str | Path) -> Path:
    from html import escape

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    d = report.as_dict()
    verdict = ("<span class='ok'>PRONTO</span>" if report.ok
               else "<span class='bad'>NON PRONTO</span>")
    parts = [f"<!doctype html><meta charset='utf-8'>"
             f"<title>Production readiness</title><style>{_CSS}</style><main>",
             f"<h1>Simulazione del ciclo di pubblicazione — {verdict}</h1>",
             f"<p class='sub'>{d['days_checked']} giorni da {escape(report.start)}, "
             f"{d['pages_checked']} pagine, fuso {escape(report.timezone)} · "
             f"generato il {escape(d['generated_at'])}</p>",
             "<div class='cards'>"]
    for label, key in (("Job simulati", "jobs_checked"),
                       ("Pubblicabili", "production_ready"),
                       ("Needs review", "needs_review"),
                       ("Date scoperte", "missing"),
                       ("Duplicati", "duplicate_jobs"),
                       ("Errori di data", "date_errors")):
        parts.append(f"<div class='card'><b>{d[key]}</b><span>{label}</span></div>")
    parts.append("</div><table><tr><th>Pagina</th><th>Tipo</th><th>Giorni</th>"
                 "<th>Pubblicabili</th><th>Contenuti distinti</th>"
                 "<th>Scoperti</th><th>Duplicati</th></tr>")
    for p in d["pages"]:
        parts.append(
            f"<tr><td>{escape(p['page_id'])}</td><td>{escape(p['content_type'])}</td>"
            f"<td>{p['days']}</td><td>{p['publishable']}</td>"
            f"<td>{p['distinct_contents']}</td><td>{p['missing']}</td>"
            f"<td>{p['duplicates']}</td></tr>")
    parts.append("</table>")
    for p in d["pages"]:
        if p["problems"]:
            parts.append(f"<h2>{escape(p['page_id'])}</h2><ul>")
            parts.extend(f"<li>{escape(x)}</li>" for x in p["problems"])
            parts.append("</ul>")
    parts.append("</main>")
    out.write_text("".join(parts), encoding="utf-8")
    return out
