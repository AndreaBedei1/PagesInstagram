"""Typer CLI for the Instagram Content Engine."""
from __future__ import annotations

import sys
from pathlib import Path


def _force_utf8_output() -> None:
    """Never let a console code page turn a report into a stack trace.

    Windows PowerShell hands Python a cp1252 stdout. A single character outside
    that page — an arrow in a summary line was enough — raises
    UnicodeEncodeError from deep inside the rendering layer, and what the
    operator sees is a traceback where a result should be. It happened during a
    real go-live, on the line describing the canary's steps.

    Fixing the offending characters one at a time treats the symptom: the next
    one gets added by someone writing a perfectly reasonable message. This makes
    the output layer unable to fail that way, whatever the caller's code page,
    and ``errors="replace"`` means the worst case is a substituted glyph rather
    than a lost command.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # already redirected, or not a real stream: nothing to do


_force_utf8_output()

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
comfy_app = typer.Typer(help="Local ComfyUI model checks")
app.add_typer(comfy_app, name="comfyui")
from .instagram_cmds import app as instagram_app  # noqa: E402
app.add_typer(instagram_app, name="instagram")
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
    um = settings.publishing.upload_method
    needs_hosting = um == "hosted_url"
    row("Upload method", True,
        f"{um}" + (" (serve hosting pubblico)" if needs_hosting
                   else " — nessun hosting, ma solo con api_flavor=facebook_login"))
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
                   approve_floor: float = typer.Option(
                       None, help="soglia di qualità; predefinito: quella del tipo")):
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


@app.command("validate-datasets")
def validate_datasets(
    expected: int = typer.Option(1000, help="elementi attesi per dataset"),
    report: str = typer.Option(None, help="percorso del report JSON"),
    skip_quality: bool = typer.Option(False, "--skip-quality",
                                      help="salta il calcolo del punteggio qualità"),
):
    """Validate every dataset in ``datasets/``. Exit code != 0 on any violation."""
    from ..content.dataset_validation import validate_all, write_report

    paths = Paths.create()
    summary = validate_all(paths.datasets, expected_items=expected,
                           check_quality=not skip_quality)
    target = Path(report) if report else paths.root / "reports" / "datasets_validation.json"
    out = write_report(summary, target)

    table = Table(title="Validazione dataset")
    for col in ("dataset", "tipo", "elementi", "errori", "avvisi", "esito"):
        table.add_column(col)
    for rep in summary.reports:
        table.add_row(rep.dataset, rep.content_type, str(rep.total),
                      str(len(rep.errors)), str(len(rep.warnings)),
                      "[green]OK[/]" if rep.ok else "[red]FAIL[/]")
    console.print(table)
    for rep in summary.reports:
        for err in rep.errors[:12]:
            console.print(f"  [red]{rep.dataset}[/]: {err}")
        if len(rep.errors) > 12:
            console.print(f"  [red]{rep.dataset}[/]: … e altri "
                          f"{len(rep.errors) - 12} errori (vedi il report)")
    console.print(f"Totale contenuti: [bold]{summary.total_items}[/]  —  report: {out}")
    raise typer.Exit(code=0 if summary.ok else 1)


@app.command("apply-review")
def apply_review(
    verdicts: str = typer.Argument(..., help="file JSON dei verdetti di revisione"),
    write: bool = typer.Option(False, "--write",
                               help="applica ai dataset (senza, è un'anteprima)"),
):
    """Apply a human review verdict file to the datasets.

    The only path that can set ``manually_verified`` or ``approved``. Nothing
    automatic writes those values.
    """
    from ..content.editorial_review import apply_verdicts

    paths = Paths.create()
    try:
        payload, reports = apply_verdicts(paths.datasets, verdicts, write=write)
    except (OSError, ValueError) as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)

    console.print(f"Revisore: [bold]{payload['reviewer']}[/] "
                  f"({payload['reviewed_at']})")
    console.print(f"[dim]{payload.get('method', '')}[/]\n")
    t = Table(title="Verdetti applicati")
    for col in ("dataset", "elementi", "verdetti fonte", "verdetti editoriali",
                "saltati"):
        t.add_column(col)
    for rep in reports:
        t.add_row(rep.dataset, str(rep.applied),
                  str(rep.by_source_verdict) if rep.by_source_verdict else "—",
                  str(rep.by_editorial_verdict) if rep.by_editorial_verdict else "—",
                  str(len(rep.skipped)))
    console.print(t)
    for rep in reports:
        for msg in rep.skipped[:5]:
            console.print(f"  [yellow]{rep.dataset}[/]: {msg}")
    if not write:
        console.print("[yellow]anteprima[/]: usa --write per applicare")


@app.command("editorial-stats")
def editorial_stats(
    report: str = typer.Option(None, help="percorso del report JSON"),
    strict: bool = typer.Option(
        False, "--strict", help="esce con codice != 0 se ci sono segnalazioni"),
):
    """Measure how repetitive the pages look over 7, 30, 90 and 365 days."""
    from ..content.editorial_report import write_stats_html, write_stats_json
    from ..content.editorial_stats import analyse_all

    paths = Paths.create()
    stats = analyse_all(paths.datasets)
    out_json = write_stats_json(stats, Path(report) if report
                                else paths.root / "reports" / "editorial_stats.json")
    out_html = write_stats_html(stats, paths.root / "reports" / "editorial_stats.html")

    t = Table(title="Ripetitività editoriale")
    for col in ("dataset", "CTA", "vuote", "combo tag", "prompt", "categorie",
                "mood", "run cat.", "run mood", "segnalazioni"):
        t.add_column(col)
    for s in stats:
        t.add_row(s.dataset.replace("_it.json", ""), str(s.distinct_cta),
                  str(s.empty_cta), str(s.distinct_hashtag_sets),
                  str(s.distinct_prompts), str(s.distinct_categories),
                  str(s.distinct_moods), str(s.longest_category_run),
                  str(s.longest_mood_run),
                  "[green]0[/]" if s.ok else f"[yellow]{len(s.warnings)}[/]")
    console.print(t)
    for s in stats:
        for wmsg in s.warnings[:6]:
            console.print(f"  [yellow]{s.dataset}[/]: {wmsg}")
        if len(s.warnings) > 6:
            console.print(f"  [yellow]{s.dataset}[/]: … e altre "
                          f"{len(s.warnings) - 6} (vedi il report)")
    console.print(f"Report: {out_json}\n        {out_html}")
    failed = any(not s.ok for s in stats)
    raise typer.Exit(code=1 if (strict and failed) else 0)


@app.command("editorial-sample")
def editorial_sample(
    per_dataset: int = typer.Option(100, help="contenuti da estrarre per dataset"),
    seed: int = typer.Option(20260805, help="seed: stesso seed, stesso campione"),
    report: str = typer.Option(None, help="percorso del report JSON"),
):
    """Extract a reproducible stratified sample for human review."""
    from ..content.dataset_io import load_dataset
    from ..content.editorial_report import (write_sample_html, write_sample_json)
    from ..content.editorial_stats import _strata, stratified_sample
    from ..content.language_check import check_item

    paths = Paths.create()
    payload = {"generated_at": __import__("datetime").datetime.now(
                   __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "seed": seed, "per_dataset": per_dataset,
               "total": 0, "datasets": []}

    for path in sorted(paths.datasets.glob("*.json")):
        data = load_dataset(path)
        items = data.get("items") or []
        ctype = data.get("content_type") or ""
        picked = stratified_sample(items, count=per_dataset, seed=seed)
        covered: set[str] = set()
        rows = []
        for idx in picked:
            item = items[idx]
            covered.update(_strata(item, idx, len(items)))
            warnings = [f"{i.rule}: {i.message}"
                        for i in check_item(item, content_type=ctype)]
            rows.append({
                "index": idx,
                "sequence_index": item.get("sequence_index"),
                "id": item.get("id"),
                "text": item.get("text"),
                "caption": item.get("caption"),
                "call_to_action": item.get("call_to_action"),
                "hashtags": item.get("hashtags"),
                "category": item.get("category"),
                "mood": item.get("mood"),
                "calendar_key": item.get("calendar_key"),
                "metadata": item.get("metadata"),
                "source_name": item.get("source_name"),
                "source_url": item.get("source_url"),
                "source_audit_status": item.get("source_audit_status"),
                "editorial_status": item.get("editorial_status"),
                "warnings": warnings,
            })
        payload["datasets"].append({
            "dataset": path.name, "content_type": ctype,
            "total_items": len(items), "items": rows,
            "strata_covered": sorted(covered),
        })
        payload["total"] += len(rows)

    out_json = write_sample_json(payload, Path(report) if report
                                 else paths.root / "reports" / "editorial_sample.json")
    out_html = write_sample_html(payload, paths.root / "reports" / "editorial_sample.html")

    t = Table(title=f"Campione editoriale (seed {seed})")
    for col in ("dataset", "estratti", "su", "strati coperti", "con avvisi"):
        t.add_column(col)
    for ds in payload["datasets"]:
        with_warnings = sum(1 for r in ds["items"] if r["warnings"])
        t.add_row(ds["dataset"].replace("_it.json", ""), str(len(ds["items"])),
                  str(ds["total_items"]), str(len(ds["strata_covered"])),
                  str(with_warnings))
    console.print(t)
    console.print(f"Totale: [bold]{payload['total']}[/] contenuti")
    console.print(f"Report: {out_json}\n        {out_html}")


@app.command("audit-sources")
def audit_sources(
    dataset: list[str] = typer.Option(
        None, "--dataset", help="dataset da controllare; default: i tre fattuali"),
    limit: int = typer.Option(
        None, help="massimo di URL NUOVI da interrogare (l'audit è riprendibile)"),
    max_age_days: int = typer.Option(
        30, help="riusa dalla cache i controlli più recenti di N giorni"),
    rate: float = typer.Option(4.0, help="richieste al secondo per host"),
    timeout: float = typer.Option(20.0, help="timeout per richiesta, in secondi"),
    retries: int = typer.Option(2, help="tentativi aggiuntivi sugli errori transitori"),
    apply: bool = typer.Option(
        False, "--apply",
        help="scrive source_audit_status nei file dei dataset"),
    refresh: bool = typer.Option(False, "--refresh", help="ignora la cache"),
):
    """Check that the cited source URLs actually resolve.

    A reachable URL is *not* a verified fact: it only rules out the sources that
    are plainly broken. Reachable items become ``reachable``; only a human can
    move them to ``manually_verified``.
    """
    import json as _json

    from ..content import source_audit as sa
    from ..content.editorial import source_status_from_check

    paths = Paths.create()
    default = ["world_curiosities_it.json", "words_of_the_day_it.json",
               "today_in_history_it.json"]
    names = list(dataset) if dataset else default
    targets = [paths.datasets / n if not Path(n).is_absolute() else Path(n)
               for n in names]
    missing = [str(p) for p in targets if not p.exists()]
    if missing:
        console.print(f"[red]dataset non trovati:[/] {', '.join(missing)}")
        raise typer.Exit(code=2)

    cache_path = paths.root / ".cache" / "source_audit_cache.json"
    cache = sa.AuditCache(cache_path, max_age_days=0 if refresh else max_age_days)
    session = sa.build_session()
    limiter = sa.make_limiter(rate)

    console.print(f"User agent: [dim]{sa.USER_AGENT}[/]")
    console.print(f"Cache: {cache_path} ({len(cache)} URL noti)")

    reports: list[sa.DatasetSourceAudit] = []
    all_checks: dict[str, sa.UrlCheck] = {}
    budget = limit
    for path in targets:
        console.print(f"[bold]{path.name}[/] …")
        seen = {"n": 0}

        def progress(chk, _seen=seen, _name=path.name):
            _seen["n"] += 1
            if _seen["n"] % 100 == 0:
                console.print(f"  {_name}: {_seen['n']} controllati")

        rep, checks = sa.audit_dataset(
            path, cache=cache, session=session, limiter=limiter,
            timeout=timeout, retries=retries, limit=budget, progress=progress)
        reports.append(rep)
        all_checks.update(checks)
        if budget is not None:
            budget = max(0, budget - rep.checked)
        console.print(f"  unici={rep.urls_unique} nuovi={rep.checked} "
                      f"cache={rep.from_cache} esiti={rep.by_status}")

    partial = any(r.by_status.get("not_checked") for r in reports)
    out_json = sa.write_json_report(reports, paths.root / "reports" / "source_audit.json",
                                    partial=partial)
    out_html = sa.write_html_report(reports, paths.root / "reports" / "source_audit.html",
                                    partial=partial)

    t = Table(title="Audit delle fonti")
    for col in ("dataset", "URL", "unici", "nuovi", "cache", "raggiungibili",
                "rotti", "transitori", "non controllati"):
        t.add_column(col)
    for r in reports:
        reach = sum(v for k, v in r.by_status.items() if k in sa.REACHABLE)
        broken = sum(v for k, v in r.by_status.items() if k in sa.BROKEN)
        trans = sum(v for k, v in r.by_status.items() if k in sa.TRANSIENT)
        t.add_row(r.dataset, str(r.urls_total), str(r.urls_unique), str(r.checked),
                  str(r.from_cache), f"[green]{reach}[/]",
                  f"[red]{broken}[/]" if broken else "0",
                  f"[yellow]{trans}[/]" if trans else "0",
                  str(r.by_status.get("not_checked", 0)))
    console.print(t)

    if apply:
        from ..content.dataset_io import load_dataset, save_dataset

        changed_total = 0
        for path in targets:
            data = load_dataset(path)
            changed = 0
            for item in data.get("items") or []:
                url = (item.get("source_url") or "").strip()
                chk = all_checks.get(url)
                if not url or chk is None:
                    continue
                # Never downgrade a human verdict with a machine result.
                if (item.get("source_audit_status") or "") == "manually_verified":
                    continue
                new_status = source_status_from_check(chk.status)
                if item.get("source_audit_status") != new_status:
                    changed += 1
                item["source_audit_status"] = new_status
                item["source_audited_at"] = chk.checked_at
                item["source_audit_note"] = (
                    f"{chk.status}"
                    + (f" HTTP {chk.http_status}" if chk.http_status else "")
                    + (f" · {chk.note}" if chk.note else ""))
            save_dataset(path, data)
            console.print(f"  {path.name}: {changed} stati aggiornati")
            changed_total += changed
        console.print(f"[bold]{changed_total}[/] elementi aggiornati nei dataset")

    console.print(f"Report: {out_json}\n        {out_html}")
    broken_total = sum(v for r in reports for k, v in r.by_status.items()
                       if k in sa.BROKEN)
    if broken_total:
        console.print(f"[red]{broken_total} URL rotti[/] — vanno corretti o sostituiti")
    raise typer.Exit(code=1 if broken_total else 0)


@app.command("security-check")
def security_check(
    include_untracked: bool = typer.Option(
        False, "--include-untracked",
        help="analizza anche i file non tracciati (NON usare su .env reali)"),
    report: str = typer.Option(None, help="percorso del report JSON"),
):
    """Scan tracked files for anything that looks like a Meta credential."""
    import json as _json

    from ..security import scan_repository

    paths = Paths.create()
    res = scan_repository(paths.root, include_untracked=include_untracked)

    console.print(f"File analizzati: [bold]{res.scanned_files}[/]")
    if res.gitignore_issues:
        for issue in res.gitignore_issues:
            console.print(f"[red]gitignore[/]: {issue}")
    else:
        console.print("[green].gitignore: .env, secrets/ e *.safetensors esclusi[/]")
    for tracked in res.tracked_env_files:
        console.print(f"[red]file .env tracciato da Git[/]: {tracked}")
    for bad in res.forbidden_tracked:
        console.print(f"[red]artefatto runtime tracciato[/] ({bad['reason']}): "
                      f"{bad['path']}")
    if res.findings:
        t = Table(title="Possibili segreti")
        for col in ("file", "riga", "regola", "estratto (redatto)"):
            t.add_column(col)
        for f in res.findings:
            t.add_row(f.path, str(f.line), f.rule, f.excerpt)
        console.print(t)
    else:
        console.print("[green]Nessun segreto rilevato nei file tracciati[/]")

    if report:
        p = Path(report)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(res.as_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        console.print(f"Report: {p}")
    raise typer.Exit(code=0 if res.ok else 1)


@app.command("preview-pages")
def preview_pages(
    per_page: int = typer.Option(5, help="anteprime per pagina"),
    comfyui: bool = typer.Option(False, help="usa ComfyUI (lento) invece del fallback"),
    out: str = typer.Option(None, help="cartella di output"),
):
    """Render N real previews per page and build a comparison index."""
    from ..monitoring.previews import build_page_previews

    paths, settings, registry, db = _ctx()
    target = Path(out) if out else paths.root / "reports" / "previews"
    result = build_page_previews(settings, db, registry, out_dir=target,
                                 per_page=per_page, try_comfyui=comfyui)
    for page_id, images in result.images.items():
        console.print(f"[green]{page_id}[/]: {len(images)} anteprime")
    console.print(f"[bold]Indice:[/] {result.index_path}")
    db.close()


@app.command("sample-review")
def sample_review(
    count: int = typer.Option(25, help="contenuti campionati per pagina"),
    out: str = typer.Option(None, help="file HTML di output"),
):
    """Build an HTML sheet with a random content sample per page for review."""
    from ..monitoring.previews import build_sample_review

    paths, settings, registry, db = _ctx()
    target = Path(out) if out else paths.root / "reports" / "sample_review.html"
    p = build_sample_review(db, registry, target, per_page=count)
    console.print(f"[green]Campione di revisione:[/] {p}")
    db.close()


@app.command()
def generate(page: str = typer.Option(..., help="page_id"),
             count: int = typer.Option(1),
             comfyui: bool = typer.Option(True, help="use ComfyUI if available")):
    """Generate the day's media for N approved contents, in the page's format.

    A page configured for IMAGE gets a 4:5 still and nothing else — no MP4, no
    music track. One configured for REELS gets the 9:16 video it always did.
    """
    from ..scheduling.pipeline import GenerationPipeline

    paths, settings, registry, db = _ctx()
    pcfg = registry.get(page)
    pipeline = GenerationPipeline(settings, db)
    made = 0
    seen: set[int] = set()
    for _ in range(count):
        content = db.pick_unused_content(page_id=page + "__preview", content_type=pcfg.content_type,
                                         min_quality=pcfg.content.minimum_quality_score)
        if not content or content["id"] in seen:
            break
        seen.add(content["id"])
        res = pipeline.generate_daily(pcfg, content, try_comfyui=comfyui)
        made += 1
        status = "[green]OK[/]" if res.ok else "[yellow]REVIEW[/]"
        if res.video_path:
            console.print(f"{status} content {content['id']} — reel/story 9:16: "
                          f"{res.video_path}"
                          + (f"  music={res.music_track_id}" if res.music_track_id
                             else " (no audio)"))
        else:
            console.print(f"{status} content {content['id']} — post 4:5: "
                          f"{res.image_path} (nessun video, nessun audio)")
    console.print(f"[green]Generati {made} contenuti[/] (media in generated/)")
    db.close()


@app.command()
def render(page: str = typer.Option(...), count: int = typer.Option(3),
           comfyui: bool = typer.Option(True)):
    """Render the still, without the rest of the pipeline, for a visual check.

    The aspect follows the page: 4:5 for a page that publishes image posts,
    9:16 for one that still publishes Reels. Rendering the wrong shape here is
    a quiet way to approve a layout nobody will ever see.
    """
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
    aspects = (["feed"] if str(pcfg.publishing.feed_media_type).upper() == "IMAGE"
               else ["reel"])
    for content in contents:
        for aspect in aspects:
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
            if stats.blocked:
                console.print(f"[red]{stats.blocked} pubblicazioni non tentate: "
                              f"Meta sta rifiutando le credenziali[/] — "
                              f"'instagram health-check' per il dettaglio")
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


# ---- comfyui sub-app ------------------------------------------------------
@comfy_app.command("status")
def comfyui_status():
    """Show the configured local model and whether ComfyUI can be reached."""
    from ..comfyui.client import ComfyUIClient

    paths = Paths.create()
    settings = load_settings(paths)
    c = settings.comfyui
    client = ComfyUIClient(c.url)
    t = Table(title="Modello locale / ComfyUI")
    t.add_column("Voce")
    t.add_column("Valore")
    t.add_row("URL", c.url)
    t.add_row("Raggiungibile", "[green]sì[/]" if client.is_ready() else "[yellow]no[/]")
    t.add_row("Famiglia modello", c.model_family)
    t.add_row("Checkpoint", c.checkpoint)
    t.add_row("Workflow", c.default_workflow)
    t.add_row("Fallback consentito", str(settings.comfyui_fallback_allowed()))
    t.add_row("Modalità", str(settings.mode))
    console.print(t)


@comfy_app.command("test-generation")
def comfyui_test_generation(
    page: str = typer.Option("pensiero_essenziale_it", help="page_id per il profilo"),
    out: str = typer.Option(None, help="percorso PNG di output"),
    allow_fallback: bool = typer.Option(
        False, help="consenti il fallback deterministico (default: NO)"),
):
    """Generate one real background through ComfyUI (proves the model works)."""
    from ..comfyui.backgrounds import BackgroundGenerator
    from ..core.errors import ComfyUIError

    paths, settings, registry, db = _ctx(migrate=False)
    db.close()
    pcfg = registry.get(page)
    gen = BackgroundGenerator(settings)
    target = Path(out) if out else paths.backgrounds / f"test_{page}.png"
    try:
        res = gen.generate(
            out_path=target, background_prompt=None, mood=None,
            profile=pcfg.visual.background_profile, aspect="reel", seed=12345,
            allow_fallback=allow_fallback, try_comfyui=True)
    except ComfyUIError as e:
        console.print(f"[red]Generazione ComfyUI fallita:[/] {e}")
        raise typer.Exit(1) from e
    color = "green" if res.source == "comfyui" else "yellow"
    console.print(f"[{color}]sorgente={res.source}[/] {res.width}x{res.height} "
                  f"seed={res.seed} -> {res.path}")
    console.print(f"checkpoint: {settings.comfyui.checkpoint} "
                  f"({settings.comfyui.model_family})")
    raise typer.Exit(code=0 if res.source == "comfyui" else 2)


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


@app.command("preproduction-smoke-test")
def preproduction_smoke_test(
    date: str = typer.Option(..., help="data locale da simulare, YYYY-MM-DD"),
    report: str = typer.Option(None, help="percorso del report JSON"),
    keep: bool = typer.Option(False, "--keep",
                              help="non cancellare la directory temporanea"),
):
    """Reproducible five-page dry-run in a throwaway database.

    Builds a database from scratch, imports the real datasets, plans the given
    date, generates the media and runs the real worker twice — then checks that
    exactly five reels published, no Stories, five distinct pages, the right
    content per page, and that the second tick published nothing.
    """
    from ..scheduling.smoke_test import run_smoke_test, write_report

    paths, settings, _registry, db = _ctx()
    db.close()
    result = run_smoke_test(settings, local_date=date, paths=paths, keep=keep,
                            progress=lambda m: console.print(f"  [dim]{m}[/]"))
    out = write_report(result, Path(report) if report
                       else paths.root / "reports" / "preproduction_smoke_test.json")

    t = Table(title=f"Smoke test di pre-produzione — {date}")
    t.add_column("controllo")
    t.add_column("esito")
    t.add_column("dettaglio")
    for check in result.checks:
        t.add_row(check.name, "[green]OK[/]" if check.ok else "[red]FAIL[/]",
                  check.detail)
    console.print(t)
    for m in result.media:
        console.print(f"  {m['page_id']:24s} {m['media_type']:5s} "
                      f"{m['size_bytes'] // 1024:>5d} KB  {m['upload_method']}")
    console.print(f"Report: {out}")
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("media-audit")
def media_audit_cmd(
    per_page: int = typer.Option(1, help="media da controllare per pagina"),
    buffer: bool = typer.Option(False, "--buffer",
                                help="controlla tutto il buffer generato"),
    all_files: bool = typer.Option(False, "--all",
                                   help="non limitare il numero per pagina"),
    file: str = typer.Option(None, "--file", help="controlla un singolo file"),
    report: str = typer.Option(None, help="percorso del report JSON"),
):
    """Measure every generated medium against the specs for its own format.

    Videos go to ffprobe and the Reel specifications; stills go to Pillow and
    the image-post specifications. Sending a PNG to ffprobe produced either
    nonsense or, on a machine without ffmpeg, a failure that said nothing about
    the file.
    """
    from ..publishing.image_audit import IMAGE_EXTENSIONS, audit_image
    from ..video.media_audit import audit_files, safe_area_box, write_report

    paths, settings, registry, db = _ctx()
    files: list[tuple[str, str]] = []
    if file:
        files.append(("(singolo)", file))
    else:
        limit = 100000 if (buffer or all_files) else per_page
        for page in registry.enabled():
            rows = db.conn.execute(
                "SELECT DISTINCT output_path FROM publication_jobs WHERE page_id=? "
                "AND output_path IS NOT NULL ORDER BY output_path LIMIT ?",
                (page.page_id, limit)).fetchall()
            for r in rows:
                if r[0] and Path(r[0]).exists():
                    files.append((page.page_id, r[0]))
    db.close()

    if not files:
        console.print("[yellow]Nessun media generato da controllare. "
                      "Esegui prima 'worker --once' o 'preproduction-smoke-test'.[/]")
        raise typer.Exit(code=1)

    stills = [(pid, f) for pid, f in files
              if Path(f).suffix.lower() in IMAGE_EXTENSIONS]
    videos = [(pid, f) for pid, f in files if (pid, f) not in stills]
    checks = audit_files(videos, ffmpeg_path=settings.video.ffmpeg_path)
    image_checks = [audit_image(f, page_id=pid) for pid, f in stills]
    out = write_report(checks, Path(report) if report
                       else paths.root / "reports" / "media_audit.json",
                       images=image_checks)

    if image_checks:
        ti = Table(title="Audit dei post immagine")
        for col in ("pagina", "file", "formato", "dimensioni", "peso", "esito"):
            ti.add_column(col)
        for c in image_checks:
            ti.add_row(c.page_id, c.file[:26], c.image_format or "?",
                       f"{c.width}×{c.height}",
                       f"{c.size_bytes / 1024:.0f} KB",
                       "[green]OK[/]" if c.ok else "[red]FAIL[/]")
        console.print(ti)
        for c in image_checks:
            for p in c.problems:
                console.print(f"  [red]{c.file}[/]: {p}")

    if not checks:
        console.print(f"Report: {out}")
        raise typer.Exit(code=0 if all(c.ok for c in image_checks) else 1)

    t = Table(title="Audit dei media")
    for col in ("pagina", "file", "contenitore", "video", "audio", "risoluzione",
                "fps", "durata", "faststart", "esito"):
        t.add_column(col)
    for c in checks:
        t.add_row(c.page_id, c.file[:26], c.container.split(",")[0],
                  f"{c.video_codec}/{c.pixel_format}",
                  f"{c.audio_codec} {c.audio_sample_rate}Hz×{c.audio_channels}",
                  f"{c.width}×{c.height}", f"{c.fps:g}",
                  f"{c.duration_seconds:g}s",
                  "[green]sì[/]" if c.faststart else "[red]no[/]",
                  "[green]OK[/]" if c.ok else "[red]FAIL[/]")
    console.print(t)
    for c in checks:
        for p in c.problems:
            console.print(f"  [red]{c.file}[/]: {p}")
    box = safe_area_box()
    console.print(f"Area sicura 9:16: x {box['left']}–{box['right']}, "
                  f"y {box['top']}–{box['bottom']} "
                  f"({box['usable_width']}×{box['usable_height']} px utilizzabili)")
    console.print(f"Report: {out}")
    raise typer.Exit(code=0 if all(c.ok for c in checks + image_checks) else 1)


@app.command("verify-corpus")
def verify_corpus_cmd(
    dataset: list[str] = typer.Option(None, "--dataset", help="limita a questi dataset"),
    rate: float = typer.Option(4.0, help="richieste al secondo"),
    report: str = typer.Option(None, help="percorso del report JSON"),
):
    """Read every cited source and attach the passage that supports each claim.

    This is the only path that can make a factual item publishable, and it
    cannot do so without evidence: a claim whose source does not contain its
    key words and numbers comes back unverified, with the reason.
    """
    from ..content.verify_corpus import (DISPATCH, cache_for, verify_all,
                                         verify_curiosities, verify_history,
                                         verify_original, verify_words,
                                         write_report)
    from ..content.dataset_io import load_dataset
    from ..content.evidence import build_session

    paths = Paths.create()
    cache_dir = paths.root / ".cache"

    def progress(done, total):
        console.print(f"    [dim]{done}/{total}[/]")

    if dataset:
        session = build_session()
        outcomes = []
        for name in dataset:
            path = paths.datasets / name
            ctype = load_dataset(path).get("content_type") or ""
            kind = DISPATCH.get(ctype)
            cache = cache_for(cache_dir, name)
            console.print(f"[bold]{name}[/] ({ctype}) …")
            if kind == "original":
                outcomes.append(verify_original(path))
            elif kind == "words":
                outcomes.append(verify_words(path, cache=cache, session=session,
                                             rate=3.0, progress=progress))
            elif kind == "history":
                outcomes.append(verify_history(path, cache=cache, session=session,
                                               rate=rate, progress=progress))
            elif kind == "curiosities":
                outcomes.append(verify_curiosities(path, cache=cache,
                                                   session=session, rate=rate,
                                                   progress=progress))
            cache.save()
    else:
        outcomes = verify_all(paths.datasets, cache_dir=cache_dir, rate=rate,
                              progress=progress)

    out = write_report(outcomes, Path(report) if report
                       else paths.root / "reports" / "verification.json")
    t = Table(title="Verifica del corpus")
    for col in ("dataset", "totale", "verificati", "falliti", "metodi"):
        t.add_column(col)
    for o in outcomes:
        t.add_row(o.dataset.replace("_it.json", ""), str(o.total),
                  f"[green]{o.verified}[/]",
                  f"[red]{o.failed}[/]" if o.failed else "0",
                  ", ".join(f"{k.split('_')[0]}={v}" for k, v in o.by_method.items()))
    console.print(t)
    for o in outcomes:
        for reason, n in list(o.by_reason.items())[:5]:
            console.print(f"  [yellow]{o.dataset}[/]: {n} × {reason}")
    total_failed = sum(o.failed for o in outcomes)
    console.print(f"Report: {out}")
    raise typer.Exit(code=1 if total_failed else 0)


@app.command("rebuild-unverified-corpus")
def rebuild_unverified_corpus(
    dataset: list[str] = typer.Option(None, "--dataset", help="limita a questi dataset"),
    limit: int = typer.Option(None, help="massimo di elementi da ricostruire"),
    resume: bool = typer.Option(True, "--resume/--no-resume",
                                help="riusa i checkpoint (predefinito)"),
    cache_dir: str = typer.Option(None, help="directory di cache e checkpoint"),
    report: str = typer.Option(None, help="percorso del report JSON"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="mostra quanti elementi verrebbero sostituiti"),
    replace: bool = typer.Option(True, "--replace/--no-replace",
                                 help="sostituisce davvero gli elementi non verificati"),
):
    """Rebuild every unverified content from a source, instead of hunting a
    source for a content that was written first.

    Keeps sequence_index and calendar_key, so the rotation and the calendar
    coverage do not move; only the claim changes, and it changes because it came
    out of the evidence.
    """
    import json as _json
    import shutil

    from ..content.dataset_io import load_dataset
    from ..content.rebuild import BUILDERS, rebuild_dataset
    from ..content.verification import is_publishable

    paths = Paths.create()
    cdir = Path(cache_dir) if cache_dir else paths.root / ".cache"
    cdir.mkdir(parents=True, exist_ok=True)
    if not resume:
        for stale in cdir.glob("rebuild_*.json"):
            stale.unlink()

    names = list(dataset) if dataset else [
        p.name for p in sorted(paths.datasets.glob("*.json"))
        if (load_dataset(p).get("content_type") or "") in BUILDERS]

    if dry_run or not replace:
        t = Table(title="Elementi da ricostruire")
        for col in ("dataset", "totale", "pronti", "da sostituire"):
            t.add_column(col)
        for name in names:
            path = paths.datasets / name
            data = load_dataset(path)
            ctype = data.get("content_type") or ""
            items = data.get("items") or []
            ready = sum(1 for i in items if is_publishable(i, content_type=ctype).ok)
            t.add_row(name, str(len(items)), str(ready), str(len(items) - ready))
        console.print(t)
        console.print("[yellow]anteprima[/]: nessun dataset modificato")
        raise typer.Exit(code=0)

    outcomes = []
    for name in names:
        path = paths.datasets / name
        console.print(f"[bold]{name}[/] …")
        outcomes.append(rebuild_dataset(
            path, cache_dir=cdir, limit=limit,
            progress=lambda d, tot: console.print(f"    [dim]{d}/{tot}[/]")))

    out = Path(report) if report else paths.root / "reports" / "rebuild.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps([o.as_dict() for o in outcomes],
                               ensure_ascii=False, indent=2), encoding="utf-8")

    t = Table(title="Ricostruzione source-first")
    for col in ("dataset", "mantenuti", "da sostituire", "ricostruiti",
                "ancora non pronti"):
        t.add_column(col)
    for o in outcomes:
        t.add_row(o.dataset.replace("_it.json", ""), str(o.kept), str(o.needed),
                  f"[green]{o.rebuilt}[/]",
                  f"[red]{o.still_failing}[/]" if o.still_failing else "0")
    console.print(t)
    for o in outcomes:
        for note in o.notes[:4]:
            console.print(f"  [dim]{o.dataset}: {note}[/]")
    console.print(f"Report: {out}")
    raise typer.Exit(code=1 if any(o.still_failing for o in outcomes) else 0)


@app.command("corpus-final-gate")
def corpus_final_gate(
    report: str = typer.Option(None, help="percorso del report JSON"),
):
    """The single check that decides whether the corpus may go to production.

    Twelve counters, all of which must be zero, plus exactly 1.000 items per
    page. Recomputes the hashes and the duplicates rather than reading a stored
    verdict, so an item edited after verification fails here.
    """
    from ..content.final_gate import run_final_gate, write_report

    paths = Paths.create()
    gate = run_final_gate(paths.datasets)
    out = write_report(gate, Path(report) if report
                       else paths.root / "reports" / "corpus_final_gate.json")

    t = Table(title="Cancello finale del corpus")
    for col in ("dataset", "elementi", "pronti", "fallimenti"):
        t.add_column(col)
    for g in gate.datasets:
        t.add_row(g.dataset.replace("_it.json", ""), str(g.items),
                  str(g.production_ready),
                  "[green]0[/]" if g.failures == 0 else f"[red]{g.failures}[/]")
    console.print(t)

    counters = gate.counters()
    for name, value in counters.items():
        colour = "green" if value == 0 else "red"
        console.print(f"  {name:24s} [{colour}]{value}[/]")
    console.print(f"\nitems: [bold]{gate.items}[/]")
    console.print(f"production_ready: [bold]{gate.production_ready}[/]")
    console.print(f"failures: [bold]{gate.failures}[/]")
    for g in gate.datasets:
        for line in g.detail[:5]:
            console.print(f"  [yellow]{g.dataset}[/]: {line}")
    console.print(f"Report: {out}")
    raise typer.Exit(code=0 if gate.ok else 1)


@app.command("production-readiness")
def production_readiness(
    from_: str = typer.Option(..., "--from", help="prima data locale, YYYY-MM-DD"),
    days: int = typer.Option(1000, help="giorni da simulare"),
    all_pages: bool = typer.Option(True, "--all-pages/--enabled-only"),
    report: str = typer.Option(None, help="percorso del report JSON"),
):
    """Simulate the whole cycle: every day, every page, through the real gate.

    Builds a database from nothing, imports the real datasets, and resolves each
    local date with the production gate on. Fails if a single day is uncovered,
    unpublishable, duplicated or unresolvable in the page's timezone.
    """
    from ..scheduling.readiness import run_readiness, write_html, write_json

    paths, settings, _registry, db = _ctx()
    db.close()
    console.print(f"[dim]simulo {days} giorni da {from_} …[/]")
    result = run_readiness(settings, paths=paths, start=from_, days=days,
                           progress=lambda m: console.print(f"  [dim]{m}[/]"))

    out_json = write_json(result, Path(report) if report
                          else paths.root / "reports" / "production_readiness.json")
    out_html = write_html(result, paths.root / "reports" / "production_readiness.html")

    t = Table(title=f"Production readiness — {days} giorni da {from_}")
    for col in ("pagina", "giorni", "pubblicabili", "distinti", "scoperti",
                "duplicati"):
        t.add_column(col)
    for p in result.pages:
        t.add_row(p.page_id, str(p.days), str(p.publishable),
                  str(p.distinct_contents),
                  f"[red]{p.missing}[/]" if p.missing else "0",
                  f"[red]{p.duplicates}[/]" if p.duplicates else "0")
    console.print(t)
    d = result.as_dict()
    for key in ("days_checked", "pages_checked", "jobs_checked",
                "production_ready", "needs_review", "blocked", "missing",
                "duplicate_jobs", "date_errors"):
        colour = "green" if (key in ("days_checked", "pages_checked",
                                     "jobs_checked", "production_ready")
                             or d[key] == 0) else "red"
        console.print(f"  {key}: [{colour}]{d[key]}[/]")
    for p in result.pages:
        for problem in p.problems[:4]:
            console.print(f"  [yellow]{p.page_id}[/]: {problem}")
    console.print(f"Report: {out_json}\n        {out_html}")
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("arm-page")
def arm_page(
    page: str = typer.Argument(..., help="page_id, oppure 'all' per tutte"),
    disarm: bool = typer.Option(False, "--disarm", help="disarma invece di armare"),
):
    """Arm a page for real publication, after its controlled canary.

    Production mode alone never publishes: a page must also be armed here.
    Every page starts disarmed and nothing in the normal run path arms one.
    """
    from ..publishing.arming import all_states, set_armed

    paths, settings, registry, db = _ctx()
    ids = registry.ids() if page == "all" else [page]
    for pid in ids:
        if pid not in registry:
            console.print(f"[red]pagina sconosciuta:[/] {pid}")
            db.close()
            raise typer.Exit(code=2)
    for pid in ids:
        state = set_armed(db, pid, not disarm)
        verb = "disarmata" if disarm else "[green]ARMATA[/]"
        console.print(f"  {pid:24s} {verb} ({state.armed_at})")

    t = Table(title="Stato di armamento")
    t.add_column("pagina"); t.add_column("stato"); t.add_column("dal")
    for s in all_states(db, registry.ids()):
        t.add_row(s.page_id, "[green]armata[/]" if s.armed else "disarmata",
                  s.armed_at or "—")
    console.print(t)
    if not disarm:
        console.print("[yellow]ICE_MODE resta invariato[/]: armare non pubblica")
    db.close()


@app.command("arming-status")
def arming_status():
    """Which pages may publish, and since when."""
    from ..publishing.arming import all_states
    from ..publishing.credential_block import block_state

    paths, settings, registry, db = _ctx()
    t = Table(title=f"Armamento — ICE_MODE={settings.mode}")
    t.add_column("pagina"); t.add_column("stato"); t.add_column("dal")
    # An armed page that Meta is refusing publishes nothing, and "armata" on its
    # own reads as "sta pubblicando". The two facts belong in the same table.
    t.add_column("credenziali")
    blocked = []
    for s in all_states(db, registry.ids()):
        cb = block_state(db, s.page_id)
        if cb:
            blocked.append((s.page_id, cb))
        t.add_row(s.page_id, "[green]armata[/]" if s.armed else "disarmata",
                  s.armed_at or "—",
                  f"[red]rifiutate dal {cb.get('since', '?')}[/]" if cb
                  else "[green]accettate[/]")
    console.print(t)
    for page_id, cb in blocked:
        console.print(f"[red]{page_id}:[/] {cb.get('message', '')}")
        console.print("  le pubblicazioni sono sospese; i job restano "
                      "pubblicabili e riprendono da soli appena Meta accetta.")
    db.close()


@app.command("regenerate-media")
def regenerate_media(
    job: int = typer.Option(None, help="job da rigenerare"),
    page: str = typer.Option(None, help="pagina (con --date)"),
    date: str = typer.Option(None, help="data locale YYYY-MM-DD (con --page)"),
    max_rounds: int = typer.Option(4, help="round di generazione da provare"),
    require_comfyui: bool = typer.Option(
        True, "--require-comfyui/--allow-fallback",
        help="fallire invece di ripiegare sullo sfondo deterministico"),
):
    """Regenerate the media of a day that failed the quality gate — only that day.

    The background seed is deterministic on purpose: the same day must produce
    the same image across restarts. The consequence was that a failed day stayed
    failed, because the three background attempts always recreate the same three
    images. ``generation_round`` is the stored escape from that, and this is the
    only command that advances it.

    Two refusals it will not negotiate: it will not touch a media that already
    passed, and it will not lower the threshold. If every round still fails it
    says so and leaves the day in review — a render below the bar is not
    published because the operator ran out of patience.
    """
    from zoneinfo import ZoneInfo

    from ..core.timeutils import parse_iso, utcnow_iso
    from ..scheduling.pipeline import GenerationPipeline

    paths, settings, registry, db = _ctx()
    if require_comfyui:
        settings.comfyui.allow_fallback_in_dry_run = False
        settings.comfyui.allow_fallback_in_test = False

    if job:
        row = db.get_job(job)
        if not row:
            console.print(f"[red]Job {job} non trovato[/]")
            db.close()
            raise typer.Exit(2)
        page_id = row["page_id"]
        pcfg = registry.get(page_id)
        local_date = parse_iso(row["scheduled_at"]).astimezone(
            ZoneInfo(pcfg.publishing.timezone)).date().isoformat()
    elif page and date:
        page_id, local_date = page, date
        pcfg = registry.get(page_id)
    else:
        console.print("[red]Serve --job N oppure --page X --date YYYY-MM-DD[/]")
        db.close()
        raise typer.Exit(2)

    daily = db.get_daily_content(page_id, local_date)
    if not daily:
        console.print(f"[red]Nessun daily_content per {page_id} {local_date}[/]")
        db.close()
        raise typer.Exit(2)

    jobs = [j for j in db.list_jobs(page_id=page_id)
            if j.get("scheduled_at") and parse_iso(j["scheduled_at"]).astimezone(
                ZoneInfo(pcfg.publishing.timezone)).date().isoformat() == local_date]
    failing = [j for j in jobs if j["status"] in (JobStatus.NEEDS_REVIEW,
                                                  JobStatus.FAILED)]
    existing = daily.get("video_path")
    if not failing and existing and Path(existing).exists():
        console.print(f"[green]Il media di {page_id} {local_date} è già valido[/]: "
                      f"non lo rigenero.")
        console.print(f"  {existing}")
        db.close()
        raise typer.Exit(0)

    content = db.get_content(daily["content_id"])
    if not content:
        console.print("[red]Il contenuto del giorno non esiste più[/]")
        db.close()
        raise typer.Exit(2)

    pipeline = GenerationPipeline(settings, db)
    start_round = int(daily.get("generation_round") or 0)
    console.print(f"{page_id} {local_date} — round attuale {start_round}, "
                  f"soglia {settings.quality.min_score}")

    # Offset 0 first: retry the round the day is already on. With the repair
    # escalation no longer truncated, the deterministic image often passes on
    # the second look, and keeping it means the buffer stays reproducible from
    # a clean checkout. Only if that still fails do new seeds come into play.
    best_score = 0.0
    for offset in range(0, max_rounds + 1):
        attempt_round = start_round + offset
        console.print(f"  [dim]round {attempt_round} …[/]")
        try:
            result = pipeline.generate_daily(
                pcfg, content, music_track_id=daily.get("music_track_id"),
                try_comfyui=True, scheduled_date=local_date,
                cycle_number=int(daily.get("cycle_number") or 0),
                generation_round=attempt_round)
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]round {attempt_round} fallito:[/] {e}")
            continue
        best_score = max(best_score, result.validation_score)
        console.print(f"    score {result.validation_score:.4f} "
                      f"{'[green]OK[/]' if result.ok else '[yellow]sotto soglia[/]'}")
        if not result.ok:
            continue

        # daily_content.video_path predates the image format and is the column
        # that holds the day's media whatever it is; media_path is whichever
        # artefact the page's format produced.
        db.update_daily_content(
            daily["id"], generation_round=attempt_round,
            media_asset_id=result.media_asset_id, video_path=result.media_path,
            music_track_id=result.music_track_id)
        for j in jobs:
            db.update_job(j["id"], content_id=content["id"],
                          output_path=result.media_path, generated_at=utcnow_iso(),
                          status=JobStatus.MEDIA_READY, last_error=None,
                          upload_method=pcfg.publishing.upload_method)
        console.print(f"[green]Rigenerato[/] al round {attempt_round}: "
                      f"score {result.validation_score:.4f} >= "
                      f"{settings.quality.min_score}")
        console.print(f"  {result.media_path}")
        db.close()
        raise typer.Exit(0)

    console.print(f"[red]Nessun round ha superato la soglia[/] "
                  f"(migliore {best_score:.4f} < {settings.quality.min_score}).")
    console.print("Il giorno resta in revisione: la soglia non si abbassa.")
    db.close()
    raise typer.Exit(1)


@app.command("buffer-status")
def buffer_status(
    from_: str = typer.Option(None, "--from",
                              help="prima data locale; default: oggi nel fuso della pagina"),
    days: int = typer.Option(30, help="giorni di copertura richiesti"),
    page: str = typer.Option(None, help="una sola pagina"),
    prune_stale: bool = typer.Option(
        False, "--prune-stale",
        help="cancella i soli file generati che nessun job referenzia"),
    older_than_days: int = typer.Option(
        1, help="con --prune-stale: non toccare nulla di più recente"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Is there enough media ahead of today, and what is left over behind it?

    The buffer on this machine was built starting from a date that has since
    gone by. Two different things follow from that and they must not be
    confused: the jobs already behind us are history and stay where they are,
    while the window that matters is the one starting today in each page's own
    timezone.

    ``--prune-stale`` removes only files under ``generated/`` that no job in the
    database points at. A file a job still references is never touched, whatever
    its date: deleting the media of a scheduled post to reclaim disk is how a
    page goes silent.
    """
    import json as _json
    from datetime import date as date_cls
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from ..core.enums import MediaType
    from ..core.timeutils import local_datetime, now_in, parse_iso, to_utc
    from ..scheduling.planner import _media_types

    paths, settings, registry, db = _ctx()
    pages = [registry.get(page)] if page else list(registry.enabled())
    now_utc_iso = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    referenced: set[str] = set()
    for row in db.conn.execute(
            "SELECT DISTINCT output_path FROM publication_jobs "
            "WHERE output_path IS NOT NULL").fetchall():
        if row[0]:
            referenced.add(str(Path(row[0]).resolve()).lower())
    for row in db.conn.execute(
            "SELECT DISTINCT video_path FROM daily_content "
            "WHERE video_path IS NOT NULL").fetchall():
        if row[0]:
            referenced.add(str(Path(row[0]).resolve()).lower())

    report: dict = {"generated_at": now_utc_iso, "days": days, "pages": []}
    covered = True
    for pcfg in pages:
        tz = pcfg.publishing.timezone
        start = (date_cls.fromisoformat(from_) if from_
                 else now_in(tz).date())
        end = start + timedelta(days=days)
        rows = db.conn.execute(
            "SELECT id, scheduled_at, status, output_path FROM publication_jobs "
            "WHERE page_id=? AND scheduled_at IS NOT NULL ORDER BY scheduled_at",
            (pcfg.page_id,)).fetchall()
        in_window = with_media = past_due = 0
        first_ready = None
        for jid, scheduled, status, out in rows:
            local_date = parse_iso(scheduled).astimezone(ZoneInfo(tz)).date()
            has_media = bool(out) and Path(out).exists()
            if scheduled < now_utc_iso:
                if status != JobStatus.PUBLISHED:
                    past_due += 1
                continue
            if start <= local_date < end:
                in_window += 1
                if has_media:
                    with_media += 1
            if has_media and first_ready is None and status != JobStatus.PUBLISHED:
                first_ready = {"job_id": jid, "scheduled_at": scheduled}
        # How many slots the window *should* contain, computed the way the
        # planner computes them and counting only the ones still ahead. A
        # today whose publication time has already gone by is not a hole in
        # the buffer, and reporting it as one makes the number untrustworthy.
        expected = 0
        for offset in range(days):
            day = start + timedelta(days=offset)
            for media_type in _media_types(pcfg):
                hh, mm = (pcfg.publishing.story_time_tuple()
                          if media_type == MediaType.STORY_VIDEO
                          else pcfg.publishing.feed_time_tuple())
                when = to_utc(local_datetime(day.year, day.month, day.day,
                                             hh, mm, tz))
                if when.strftime("%Y-%m-%dT%H:%M:%SZ") > now_utc_iso:
                    expected += 1
        page_ok = with_media >= expected and first_ready is not None
        covered = covered and page_ok
        report["pages"].append({
            "page_id": pcfg.page_id, "from": start.isoformat(),
            "jobs_in_window": in_window, "with_media": with_media,
            "expected": expected, "past_due_unpublished": past_due,
            "first_future_ready": first_ready, "ok": page_ok})

    # Two conditions, not one: nothing points at the file *and* it has been
    # sitting there for a day. A run in progress writes intermediates the
    # database does not know about yet, and deleting those mid-generation
    # produces a failure that looks like a bug in the renderer.
    cutoff = __import__("time").time() - older_than_days * 86400
    stale = [p for p in sorted(paths.generated.rglob("*"))
             if p.is_file() and str(p.resolve()).lower() not in referenced
             and p.stat().st_mtime < cutoff]
    by_dir: dict[str, int] = {}
    for p in stale:
        key = str(p.parent.relative_to(paths.generated)) or "."
        by_dir[key] = by_dir.get(key, 0) + 1
    report["stale_files"] = len(stale)
    report["stale_bytes"] = sum(p.stat().st_size for p in stale)
    report["stale_by_directory"] = by_dir
    if prune_stale:
        removed = 0
        for p in stale:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        report["stale_removed"] = removed

    db.close()
    if as_json:
        console.print_json(_json.dumps(report))
        raise typer.Exit(0 if covered else 1)

    t = Table(title=f"Buffer — {days} giorni richiesti")
    for col in ("pagina", "da", "job in finestra", "con media", "attesi",
                "scaduti non pubblicati", "primo job futuro pronto"):
        t.add_column(col)
    for p in report["pages"]:
        ready = p["first_future_ready"]
        t.add_row(p["page_id"], p["from"], str(p["jobs_in_window"]),
                  ("[green]" if p["ok"] else "[red]") + str(p["with_media"]) + "[/]",
                  str(p["expected"]),
                  ("[yellow]" + str(p["past_due_unpublished"]) + "[/]"
                   if p["past_due_unpublished"] else "0"),
                  f"{ready['job_id']} @ {ready['scheduled_at']}" if ready else "[red]nessuno[/]")
    console.print(t)
    console.print(f"file generati che nessun job referenzia, più vecchi di "
                  f"{older_than_days} g: {report['stale_files']} "
                  f"({report['stale_bytes'] / 1_000_000:.1f} MB)"
                  + (f" — rimossi {report.get('stale_removed', 0)}"
                     if prune_stale else " (usa --prune-stale per rimuoverli)"))
    for directory, count in sorted(report["stale_by_directory"].items()):
        console.print(f"  [dim]{directory}: {count}[/]")
    if not covered:
        console.print("[red]Buffer insufficiente.[/] Rigeneralo dalla data odierna:")
        console.print("  python -m src.cli prepare-buffer --from "
                      f"{report['pages'][0]['from'] if report['pages'] else '<data>'} "
                      f"--days {days} --all-pages --require-comfyui")
    raise typer.Exit(0 if covered else 1)


@app.command("prepare-buffer")
def prepare_buffer(
    from_: str = typer.Option(None, "--from",
                              help="prima data locale; default: oggi nel fuso della pagina"),
    days: int = typer.Option(30, help="giorni di buffer"),
    all_pages: bool = typer.Option(True, "--all-pages/--enabled-only"),
    require_comfyui: bool = typer.Option(
        False, "--require-comfyui",
        help="fallisce invece di usare lo sfondo di ripiego"),
):
    """Generate the rolling media buffer for real, without publishing anything.

    With ``--require-comfyui`` a background that falls back to the deterministic
    gradient is a failure, not a silent downgrade: a buffer built on the fallback
    looks fine in a report and wrong on the account.
    """
    from ..scheduling.planner import plan_page
    from ..scheduling.worker import Worker

    paths, settings, registry, db = _ctx()
    if require_comfyui:
        settings.comfyui.allow_fallback_in_dry_run = False
        settings.comfyui.allow_fallback_in_test = False
        from ..comfyui.client import ComfyUIClient
        if not ComfyUIClient(settings.comfyui.url).is_ready():
            console.print("[red]ComfyUI non raggiungibile[/] e il fallback è "
                          "vietato: avvialo prima di generare il buffer.")
            db.close()
            raise typer.Exit(code=1)

    from datetime import date as date_cls

    from ..core.timeutils import now_in

    planned = existing = 0
    for page in registry.enabled():
        # Each page keeps its own timezone, and "today" is a local question.
        start = (date_cls.fromisoformat(from_) if from_
                 else now_in(page.publishing.timezone).date())
        rep = plan_page(db, page, start=start, days=days)
        planned += rep.created
        existing += rep.existing
    console.print(f"pianificati {planned} job su {days} giorni "
                  f"({existing} già presenti: il comando è idempotente)")

    worker = Worker(settings, db, registry, try_comfyui=True,
                    plan_enabled=False, cleanup_enabled=False,
                    prepare_ahead_minutes=days * 24 * 60)
    stats = worker._prepare_media  # noqa: SLF001 - the tick's media phase only
    from ..scheduling.worker import TickStats
    tick = TickStats()
    stats(tick)
    console.print(f"preparati={tick.prepared} falliti={tick.failed} "
                  f"review={tick.review}")
    for message in tick.messages[:5]:
        console.print(f"  [yellow]{message}[/]")
    db.close()
    raise typer.Exit(code=0 if tick.failed == 0 and tick.review == 0 else 1)
