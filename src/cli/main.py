"""Typer CLI for the Instagram Content Engine."""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..accounts import load_pages
from ..core.enums import JobStatus, Mode
from ..core.logging_setup import setup_logging
from ..core.paths import Paths
from ..core.settings import load_settings
from ..database import Database

app = typer.Typer(help="Instagram Content Engine — multi-account content generator & publisher",
                  no_args_is_help=True, add_completion=False)
music_app = typer.Typer(help="Manage the music library")
app.add_typer(music_app, name="music")
console = Console()


def _ctx(mode: str | None = None, *, migrate: bool = True):
    paths = Paths.create()
    settings = load_settings(paths)
    if mode:
        settings.mode = Mode(mode)
    setup_logging(paths.logs, settings.logging.level, console=False)
    registry = load_pages(paths)
    db = Database.open(settings.db_path(),
                       busy_timeout_ms=settings.database.busy_timeout_ms, migrate=migrate)
    return paths, settings, registry, db


# =========================================================================
@app.command()
def validate():
    """Check environment: config, ffmpeg, ComfyUI, fonts, datasets, DB, pages."""
    from ..video.ffmpeg import resolve_ffmpeg, probe_media  # noqa: F401
    from ..comfyui.client import ComfyUIClient
    from ..rendering.fonts import FontResolver

    paths = Paths.create()
    settings = load_settings(paths)
    table = Table(title="Validazione ambiente")
    table.add_column("Check")
    table.add_column("Stato")
    table.add_column("Dettaglio")
    ok = True

    def row(name, good, detail):
        nonlocal ok
        ok = ok and (good is not False)
        mark = "[green]OK[/]" if good is True else ("[yellow]WARN[/]" if good is None else "[red]FAIL[/]")
        table.add_row(name, mark, detail)

    row("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        ff = resolve_ffmpeg(settings.video.ffmpeg_path)
        row("FFmpeg", True, ff)
    except Exception as e:  # noqa: BLE001
        row("FFmpeg", False, str(e))
    fonts = FontResolver(paths.fonts).available()
    missing_fonts = [k for k, v in fonts.items() if not v]
    row("Fonts", None if missing_fonts else True,
        f"sans={bool(fonts.get('sans'))} serif={bool(fonts.get('serif'))}")
    comfy = ComfyUIClient(settings.comfyui.url)
    row("ComfyUI", None if not comfy.is_ready() else True,
        f"{settings.comfyui.url} ({'reachable' if comfy.is_ready() else 'not running — fallback used'})")
    datasets = list(paths.datasets.glob("*.json")) if paths.datasets.exists() else []
    row("Datasets", bool(datasets), f"{len(datasets)} file")
    try:
        registry = load_pages(paths)
        row("Pages", len(registry) > 0, f"{len(registry)}: {', '.join(registry.ids())}")
    except Exception as e:  # noqa: BLE001
        row("Pages", False, str(e))
    try:
        db = Database.open(settings.db_path())
        db.close()
        row("Database", True, str(settings.db_path()))
    except Exception as e:  # noqa: BLE001
        row("Database", False, str(e))
    row("Mode", True, str(settings.mode))
    console.print(table)
    raise typer.Exit(code=0 if ok else 1)


@app.command("init-db")
def init_db():
    """Create/upgrade the database and register pages."""
    paths, settings, registry, db = _ctx()
    for page in registry.all():
        db.upsert_page(page.page_id, page.display_name, page.content_type,
                       page.enabled, page.config_path)
    console.print(f"[green]Database pronto[/]: {settings.db_path()} — {len(registry)} pagine")
    db.close()


@app.command("import-content")
def import_content(dataset: str = typer.Option(None, help="Specific dataset file; default: all datasets/*.json"),
                   approve_floor: float = typer.Option(0.80)):
    """Import dataset JSON files into the DB (dedup + quality gating)."""
    from ..content.importer import import_dataset

    paths, settings, registry, db = _ctx()
    files = [Path(dataset)] if dataset else sorted(paths.datasets.glob("*.json"))
    if not files:
        console.print("[yellow]Nessun dataset trovato in datasets/[/]")
        raise typer.Exit(1)
    for f in files:
        rep = import_dataset(db, f, approve_floor=approve_floor)
        console.print(rep.summary())
    db.close()


@app.command()
def generate(page: str = typer.Option(..., help="page_id"),
             count: int = typer.Option(1),
             comfyui: bool = typer.Option(True, help="use ComfyUI if available")):
    """Generate media (background + post/story images + videos) for N approved contents."""
    from ..scheduling.pipeline import GenerationPipeline

    paths, settings, registry, db = _ctx()
    pcfg = registry.get(page)
    pipeline = GenerationPipeline(settings, db)
    aspects = []
    if pcfg.publishing.publish_feed:
        aspects.append("feed")
    if pcfg.publishing.publish_story:
        aspects.append("story")
    made = 0
    seen: set[int] = set()
    for _ in range(count):
        content = db.pick_unused_content(page_id=page + "__preview", content_type=pcfg.content_type,
                                         min_quality=pcfg.content.minimum_quality_score)
        if not content or content["id"] in seen:
            break
        seen.add(content["id"])
        res = pipeline.generate(pcfg, content, aspects=aspects, try_comfyui=comfyui)
        made += 1
        status = "[green]OK[/]" if res.ok else "[yellow]REVIEW[/]"
        console.print(f"{status} content {content['id']} — " +
                      ", ".join(f"{a}:{o.video_path}" for a, o in res.outputs.items()))
    console.print(f"[green]Generati {made} contenuti[/] (media in generated/)")
    db.close()


@app.command()
def render(page: str = typer.Option(...), count: int = typer.Option(3),
           comfyui: bool = typer.Option(True)):
    """Render only images (no video) for a quick visual check."""
    from ..comfyui.backgrounds import BackgroundGenerator
    from ..quality.validator import MediaValidator
    from ..rendering.renderer import Renderer

    paths, settings, registry, db = _ctx()
    pcfg = registry.get(page)
    bg = BackgroundGenerator(settings)
    renderer = Renderer(settings)
    validator = MediaValidator(settings)
    contents = db.list_contents(content_type=pcfg.content_type,
                                status="approved_for_publication")[:count]
    for content in contents:
        for aspect in (["feed", "story"] if pcfg.publishing.publish_story else ["feed"]):
            stem = f"{page}_{content['id']}_{aspect}"
            bgres = bg.generate(out_path=paths.backgrounds / f"{stem}_bg.png",
                                background_prompt=content.get("background_prompt"),
                                mood=content.get("mood"),
                                profile=pcfg.visual.background_profile, aspect=aspect,
                                try_comfyui=comfyui)
            out = (paths.posts if aspect == "feed" else paths.stories) / f"{stem}.png"

            def rf(opts, _bg=bgres.path, _out=out, _a=aspect):
                return renderer.render(background_path=_bg, out_path=_out, text=content["text"],
                                       content_type=pcfg.content_type, aspect=_a,
                                       author=content.get("author"),
                                       show_author=pcfg.visual.show_author,
                                       logo_text=pcfg.visual.logo_text if pcfg.visual.logo_enabled else None,
                                       options=opts)
            res, val, _ = validator.render_until_valid(rf)
            console.print(f"{'[green]OK[/]' if val.passed else '[yellow]LOW[/]'} "
                          f"{aspect} content {content['id']} score={val.score} -> {res.path}")
    db.close()


@app.command()
def schedule(days: int = typer.Option(1, help="plan N days ahead")):
    """Plan daily publication jobs for all enabled pages."""
    from ..scheduling.planner import plan_jobs

    paths, settings, registry, db = _ctx()
    rep = plan_jobs(db, registry, settings, days=days)
    console.print(f"[green]{rep.summary()}[/]")
    db.close()


@app.command()
def worker(interval: float = typer.Option(60.0), once: bool = typer.Option(False, help="run one tick and exit"),
           comfyui: bool = typer.Option(True)):
    """Run the persistent worker (plan -> generate -> publish)."""
    import threading

    from ..scheduling.lock import SingleInstanceLock
    from ..scheduling.worker import Worker

    paths, settings, registry, db = _ctx()
    lock = SingleInstanceLock(paths.logs / "worker.lock")
    if not lock.acquire():
        console.print("[red]Un altro worker è già in esecuzione (lock attivo).[/]")
        raise typer.Exit(1)
    try:
        w = Worker(settings, db, registry, try_comfyui=comfyui)
        if once:
            stats = w.run_once()
            console.print(f"[green]tick[/] published={stats.published} prepared={stats.prepared} "
                          f"skipped={stats.skipped} review={stats.review} failed={stats.failed}")
        else:
            stop = threading.Event()
            try:
                w.run_forever(interval, stop)
            except KeyboardInterrupt:
                stop.set()
                console.print("Worker interrotto")
    finally:
        lock.release()
        db.close()


@app.command("publish-next")
def publish_next(page: str = typer.Option(None), dry_run: bool = typer.Option(False, "--dry-run")):
    """Prepare and publish the next due job (respects --dry-run)."""
    from ..scheduling.worker import Worker

    paths, settings, registry, db = _ctx(mode="dry_run" if dry_run else None)
    w = Worker(settings, db, registry)
    from ..scheduling.worker import TickStats
    w._prepare_media(TickStats())
    jobs = db.list_jobs(statuses=[JobStatus.MEDIA_READY], page_id=page)
    if not jobs:
        console.print("[yellow]Nessun job pronto (MEDIA_READY).[/] Esegui prima 'schedule'.")
        raise typer.Exit(0)
    outcome = w.publisher.publish_job(jobs[0]["id"])
    console.print(f"job {jobs[0]['id']}: [bold]{outcome.status}[/] — {outcome.message}")
    db.close()


@app.command("retry-failed")
def retry_failed():
    """Re-queue FAILED jobs for another attempt."""
    from ..core.timeutils import utcnow_iso

    paths, settings, registry, db = _ctx()
    failed = db.list_jobs(status=JobStatus.FAILED)
    for j in failed:
        db.update_job(j["id"], status=JobStatus.RETRY_PENDING, next_retry_at=utcnow_iso(),
                      last_error=None)
    console.print(f"[green]{len(failed)} job rimessi in coda[/]")
    db.close()


@app.command()
def status():
    """Show a status summary."""
    from ..monitoring import status_summary

    paths, settings, registry, db = _ctx()
    s = status_summary(db)
    console.print(f"[bold]Mode:[/] {settings.mode}   [bold]Music tracks:[/] {s['music_tracks']}")
    for title, key in [("Contenuti per stato", "contents_by_status"),
                       ("Job per stato", "jobs_by_status")]:
        t = Table(title=title)
        t.add_column("stato")
        t.add_column("n", justify="right")
        for k, v in sorted(s[key].items()):
            t.add_row(str(k), str(v))
        console.print(t)
    if s["upcoming_jobs"]:
        t = Table(title="Prossimi job")
        for c in ("page_id", "media_type", "status", "scheduled_at"):
            t.add_column(c)
        for j in s["upcoming_jobs"]:
            t.add_row(j["page_id"], j["media_type"], j["status"], j["scheduled_at"] or "")
        console.print(t)
    db.close()


@app.command()
def preview(limit: int = typer.Option(30)):
    """Build a local HTML gallery of generated media."""
    from ..monitoring import build_preview_html

    paths, settings, registry, db = _ctx()
    out = build_preview_html(db, paths, limit=limit)
    console.print(f"[green]Preview:[/] {out}")
    db.close()


@app.command("export-report")
def export_report_cmd(out: str = typer.Option(None)):
    """Export a JSON status report."""
    from ..monitoring import export_report

    paths, settings, registry, db = _ctx()
    target = Path(out) if out else paths.logs / "report.json"
    p = export_report(db, target)
    console.print(f"[green]Report:[/] {p}")
    db.close()


@app.command()
def dashboard(host: str = typer.Option(None), port: int = typer.Option(None)):
    """Start the local review dashboard (FastAPI)."""
    from ..dashboard.app import run_dashboard

    paths, settings, registry, db = _ctx()
    db.close()
    run_dashboard(settings, host=host, port=port)


@app.command()
def pages():
    """List configured pages."""
    paths, settings, registry, db = _ctx(migrate=False)
    t = Table(title="Pagine")
    for c in ("page_id", "type", "enabled", "feed", "story", "show_author"):
        t.add_column(c)
    for p in registry.all():
        t.add_row(p.page_id, p.content_type, str(p.enabled),
                  p.publishing.feed_time, p.publishing.story_time, str(p.visual.show_author))
    console.print(t)
    db.close()


# ---- music sub-app --------------------------------------------------------
@music_app.command("generate")
def music_generate(per_mood: int = typer.Option(1), duration: float = typer.Option(18.0)):
    """Synthesize CC0 placeholder tones (one/two per mood) and write the catalog."""
    from ..music.library import generate_placeholder_library

    paths, settings, registry, db = _ctx()
    rep = generate_placeholder_library(paths.music, duration=duration, per_mood=per_mood)
    console.print(f"[green]{rep.summary()}[/]")
    db.close()


@music_app.command("sync")
def music_sync():
    """Load catalog.json into the DB (license-gated)."""
    from ..music.library import sync_library

    paths, settings, registry, db = _ctx()
    rep = sync_library(db, paths.music)
    console.print(f"[green]{rep.summary()}[/]")
    db.close()


if __name__ == "__main__":
    app()
