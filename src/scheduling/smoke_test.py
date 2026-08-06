"""A five-page dry-run that anyone can reproduce, on any machine, in one command.

The previous release reported a successful five-page dry-run. It was real, but
it was produced by hand-editing timestamps in the working database and then
restoring a backup — so the evidence was a paragraph in a report, and nobody
else could re-run it.

This builds the whole thing from nothing:

1. a temporary database, migrated from scratch;
2. the five real page configurations;
3. the real datasets, imported through the real importer;
4. one explicit date, planned through the real planner;
5. media generated through the real pipeline (with the deterministic background
   fallback, so no GPU and no ComfyUI are required in CI);
6. the five jobs made due;
7. the real worker tick.

Then it checks what actually happened: exactly five main jobs, exactly zero
Stories, five distinct pages, the content each page's policy says belongs to
that date, and — by running the tick a second time — that nothing publishes
twice.

The temporary directory is removed unless ``keep`` is set. The report is an
artifact, not a commit: CI uploads ``reports/preproduction_smoke_test.json``
rather than storing it in the repository, because it embeds absolute paths and
a timestamp and would change on every run.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core.enums import JobStatus, MediaType, Mode
from ..core.settings import Settings
from ..database import Database
from .worker import Worker

EXPECTED_PAGES = 5


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SmokeResult:
    date: str
    mode: str = ""
    workdir: str = ""
    pages: list[str] = field(default_factory=list)
    contents_imported: int = 0
    contents_approved: int = 0
    jobs_planned: int = 0
    published_first_tick: int = 0
    published_second_tick: int = 0
    media: list[dict] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name=name, ok=ok, detail=detail))

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "date": self.date,
            "mode": self.mode,
            "workdir": self.workdir,
            "pages": self.pages,
            "contents_imported": self.contents_imported,
            "contents_approved": self.contents_approved,
            "jobs_planned": self.jobs_planned,
            "published_first_tick": self.published_first_tick,
            "published_second_tick": self.published_second_tick,
            "media": self.media,
            "checks": [c.as_dict() for c in self.checks],
            "messages": self.messages,
        }


def _make_due(db: Database, local_date: str, minutes_ago: int = 5) -> int:
    """Move the day's jobs into the past so the tick considers them due."""
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = db.conn.execute(
        "UPDATE publication_jobs SET scheduled_at=? WHERE date(scheduled_at)=?",
        (stamp, local_date))
    db.conn.commit()
    return cur.rowcount


def run_smoke_test(settings: Settings, *, local_date: str, paths,
                   keep: bool = False, progress=None) -> SmokeResult:
    """Run the whole five-page dry-run in a throwaway database."""
    from ..accounts.registry import load_pages
    from ..content.importer import import_dataset
    from ..content.selection import select_for_date
    from .planner import plan_page

    result = SmokeResult(date=local_date, mode=str(settings.mode))
    if settings.mode != Mode.DRY_RUN:
        result.add("modalita_dry_run", False,
                   f"ICE_MODE={settings.mode}: lo smoke test gira solo in dry_run")
        return result

    workdir = Path(tempfile.mkdtemp(prefix="ice-smoke-"))
    result.workdir = str(workdir)

    def say(msg: str) -> None:
        result.messages.append(msg)
        if progress:
            progress(msg)

    try:
        # 1 — a database that has never existed before
        db_path = workdir / "smoke.sqlite"
        db = Database.open(db_path, migrate=True)
        say(f"database temporaneo: {db_path}")

        # 2 — the five real pages
        registry = load_pages(paths)
        pages = registry.enabled()
        result.pages = [p.page_id for p in pages]
        result.add("cinque_pagine", len(pages) == EXPECTED_PAGES,
                   f"{len(pages)} pagine caricate: {', '.join(result.pages)}")
        for page in pages:
            db.upsert_page(page.page_id, page.display_name, page.content_type,
                           enabled=True, configuration_path=page.config_path)

        # 3 — the real datasets through the real importer
        for path in sorted(Path(paths.datasets).glob("*.json")):
            rep = import_dataset(db, path)
            result.contents_imported += rep.added
            result.contents_approved += rep.approved
            say(f"{path.name}: {rep.added} importati, {rep.approved} approvati")
        result.add("contenuti_importati", result.contents_imported > 0,
                   f"{result.contents_imported} contenuti, "
                   f"{result.contents_approved} approvati")

        # 4 — plan exactly one day, the one asked for. plan_jobs() starts from
        # "today" in each page's timezone, which is right for the worker and
        # wrong for a test that must give the same answer next week.
        start = datetime.strptime(local_date, "%Y-%m-%d").date()
        planned = 0
        for page in pages:
            planned += plan_page(db, page, start=start, days=1).created
        result.jobs_planned = planned
        say(f"pianificati {planned} job per il {local_date}")
        result.add("cinque_job_pianificati", planned == EXPECTED_PAGES,
                   f"{planned} job pianificati (attesi {EXPECTED_PAGES})")

        rows = db.conn.execute(
            "SELECT page_id, media_type FROM publication_jobs "
            "WHERE date(scheduled_at)=?", (local_date,)).fetchall()
        media_types = [r[1] for r in rows]
        result.add("nessuna_storia",
                   all(m != MediaType.STORY_VIDEO.value for m in media_types),
                   f"tipi di media pianificati: {sorted(set(media_types))}")
        result.add("cinque_pagine_distinte",
                   len({r[0] for r in rows}) == EXPECTED_PAGES,
                   f"{len({r[0] for r in rows})} pagine distinte")

        # 6 & 7 — make them due and run the real worker
        moved = _make_due(db, local_date)
        say(f"{moved} job resi immediatamente dovuti")
        worker = Worker(settings, db, registry, try_comfyui=False,
                        cleanup_enabled=False, plan_enabled=False)
        stats = worker.run_once()
        result.published_first_tick = stats.published
        say(f"primo tick: published={stats.published} prepared={stats.prepared} "
            f"failed={stats.failed} review={stats.review}")
        result.add("cinque_pubblicazioni", stats.published == EXPECTED_PAGES,
                   f"published={stats.published} failed={stats.failed} "
                   f"review={stats.review}")

        published = db.conn.execute(
            "SELECT page_id, media_type, output_path, remote_media_id, upload_method "
            "FROM publication_jobs WHERE status=? ORDER BY page_id",
            (JobStatus.PUBLISHED.value,)).fetchall()
        for r in published:
            path = Path(r[2]) if r[2] else None
            result.media.append({
                "page_id": r[0], "media_type": r[1],
                "file": path.name if path else None,
                "size_bytes": path.stat().st_size if path and path.exists() else 0,
                "remote_media_id": r[3], "upload_method": r[4],
            })
        result.add("tutti_resumable",
                   all(m["upload_method"] == "resumable" for m in result.media),
                   f"metodi di upload: {sorted({m['upload_method'] for m in result.media})}")
        # Which content each page should carry. Making a job due rewrites its
        # scheduled_at to "now", so the day the worker resolves is today, not
        # the date that was planned — the check has to ask the policy about the
        # same day the worker saw, or it compares two different questions.
        from zoneinfo import ZoneInfo

        from ..core.timeutils import parse_iso
        wrong: list[str] = []
        for page in pages:
            row = db.conn.execute(
                "SELECT content_id, scheduled_at FROM publication_jobs "
                "WHERE page_id=? AND status=? LIMIT 1",
                (page.page_id, JobStatus.PUBLISHED.value)).fetchone()
            if not row or row[0] is None:
                wrong.append(f"{page.page_id}: nessun job pubblicato")
                continue
            effective = parse_iso(row[1]).astimezone(
                ZoneInfo(page.publishing.timezone)).date().isoformat()
            try:
                sel = select_for_date(db, page, effective)
            except Exception as e:                       # noqa: BLE001
                wrong.append(f"{page.page_id}: {e}")
                continue
            if row[0] != sel.content_id:
                wrong.append(f"{page.page_id} ({effective}): job={row[0]} "
                             f"policy={sel.content_id}")
        result.add("contenuto_corretto_per_pagina", not wrong,
                   "; ".join(wrong) if wrong else
                   "ogni job porta il contenuto che la policy assegna a quel giorno")

        # And the planned date resolves deterministically for all five pages.
        unstable: list[str] = []
        for page in pages:
            try:
                a = select_for_date(db, page, local_date).content_id
                b = select_for_date(db, page, local_date).content_id
            except Exception as e:                       # noqa: BLE001
                unstable.append(f"{page.page_id}: {e}")
                continue
            if a != b:
                unstable.append(f"{page.page_id}: {a} != {b}")
        result.add("selezione_deterministica", not unstable,
                   "; ".join(unstable) if unstable else
                   f"le cinque pagine risolvono {local_date} in modo stabile")

        result.add("media_non_vuoti",
                   all(m["size_bytes"] > 0 for m in result.media),
                   f"dimensioni: {[m['size_bytes'] for m in result.media]}")

        # idempotence: a second tick must publish nothing
        stats2 = worker.run_once()
        result.published_second_tick = stats2.published
        result.add("idempotente", stats2.published == 0,
                   f"il secondo tick ha pubblicato {stats2.published} elementi "
                   f"(atteso 0)")
        total_published = db.conn.execute(
            "SELECT COUNT(*) FROM publication_jobs WHERE status=?",
            (JobStatus.PUBLISHED.value,)).fetchone()[0]
        result.add("nessun_doppione", total_published == EXPECTED_PAGES,
                   f"{total_published} job pubblicati in totale "
                   f"(attesi {EXPECTED_PAGES})")
        db.close()
    finally:
        if keep:
            result.messages.append(f"directory conservata: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return result


def write_report(result: SmokeResult, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
