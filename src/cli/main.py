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
        f"{um}" + (" (serve hosting pubblico)" if needs_hosting else " — nessun hosting/porta richiesti"))
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
    """Generate media (background + post/story images + videos) for N approved contents."""
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
        # Produces the single 9:16 Reel video (shared by Reel + Story).
        res = pipeline.generate_daily(pcfg, content, try_comfyui=comfyui)
        made += 1
        status = "[green]OK[/]" if res.ok else "[yellow]REVIEW[/]"
        console.print(f"{status} content {content['id']} — reel/story 9:16: {res.video_path}"
                      + (f"  music={res.music_track_id}" if res.music_track_id else " (no audio)"))
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
        for aspect in ["reel"]:   # main content is a 9:16 Reel (shared with Story)
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
    per_page: int = typer.Option(1, help="video da controllare per pagina"),
    report: str = typer.Option(None, help="percorso del report JSON"),
):
    """Probe generated videos with ffprobe against Meta's Reel specifications."""
    from ..video.media_audit import audit_files, safe_area_box, write_report

    paths, settings, registry, db = _ctx()
    files: list[tuple[str, str]] = []
    for page in registry.enabled():
        rows = db.conn.execute(
            "SELECT output_path FROM publication_jobs WHERE page_id=? "
            "AND output_path IS NOT NULL ORDER BY id DESC LIMIT ?",
            (page.page_id, per_page)).fetchall()
        for r in rows:
            if r[0] and Path(r[0]).exists():
                files.append((page.page_id, r[0]))
    db.close()

    if not files:
        console.print("[yellow]Nessun media generato da controllare. "
                      "Esegui prima 'worker --once' o 'preproduction-smoke-test'.[/]")
        raise typer.Exit(code=1)

    checks = audit_files(files, ffmpeg_path=settings.video.ffmpeg_path)
    out = write_report(checks, Path(report) if report
                       else paths.root / "reports" / "media_audit.json")

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
    raise typer.Exit(code=0 if all(c.ok for c in checks) else 1)


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
