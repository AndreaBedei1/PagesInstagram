"""`ice instagram ...` — Meta account / token / resumable-upload operations.

These commands talk to the real Meta API when credentials are present (env). They
are safe by default: upload-test never publishes; publish-job requires --confirm.
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..accounts import load_pages
from ..core.enums import JobStatus, MediaType, Mode, UploadStatus
from ..core.errors import PublishError
from ..core.logging_setup import get_logger, setup_logging
from ..core.paths import Paths
from ..core.settings import load_settings
from ..core.timeutils import utcnow_iso
from ..database import Database
from ..publishing import Publisher, build_publish_target

app = typer.Typer(help="Meta account, token and direct resumable-upload commands",
                  no_args_is_help=True)
console = Console()
log = get_logger("cli.instagram")


def upload_would_publish(mode: Mode, *, publish: bool,
                         confirm: bool) -> tuple[bool, str]:
    """Decide whether ``upload-test`` may call ``media_publish``.

    One function, so there is exactly one place a refactor could get this wrong
    and exactly one place the tests have to watch. It answers ``False`` unless
    the caller asked to publish **and**, outside dry-run, confirmed it.

    In ``dry_run`` the answer is always ``False``: that mode exists precisely so
    that nothing reaches a real account, and a flag on a command must not be
    able to override the mode.
    """
    if not publish:
        return False, "richiesto --publish per pubblicare"
    if mode == Mode.DRY_RUN:
        return False, "ICE_MODE=dry_run: nessuna pubblicazione reale"
    if not confirm:
        return False, (f"ICE_MODE={mode}: serve anche --confirm per una "
                       f"pubblicazione reale")
    return True, "pubblicazione autorizzata esplicitamente"


def _ctx(page_id: str):
    paths = Paths.create()
    settings = load_settings(paths)
    setup_logging(paths.logs, settings.logging.level, console=False)
    reg = load_pages(paths)
    page = reg.get(page_id)
    db = Database.open(settings.db_path())
    return paths, settings, reg, page, db


@app.command("check-config")
def check_config(page: str = typer.Option(..., help="page_id")):
    """Validate a page's publishing configuration (no network)."""
    _, s, _, pcfg, db = _ctx(page)
    t = Table(title=f"Config publishing — {page}")
    t.add_column("chiave")
    t.add_column("valore")
    t.add_row("mode", str(s.mode))
    t.add_row("upload_method", pcfg.publishing.upload_method)
    t.add_row("feed_media_type", pcfg.publishing.feed_media_type)
    t.add_row("share_reel_to_feed", str(pcfg.publishing.share_reel_to_feed))
    t.add_row("story_media_type", pcfg.publishing.story_media_type)
    t.add_row("api_flavor", pcfg.instagram.api_flavor)
    t.add_row("account_type (atteso)", pcfg.instagram.account_type)
    prefix = pcfg.env_prefix()
    has_uid = bool(os.environ.get(f"{prefix}_IG_USER_ID"))
    has_tok = bool(os.environ.get(f"{prefix}_ACCESS_TOKEN") or os.environ.get("META_ACCESS_TOKEN"))
    t.add_row("IG_USER_ID (env)", "[green]set[/]" if has_uid else "[red]missing[/]")
    t.add_row("ACCESS_TOKEN (env)", "[green]set[/]" if has_tok else "[red]missing[/]")
    needs_hosting = pcfg.publishing.upload_method == "hosted_url"
    t.add_row("richiede hosting pubblico", "sì" if needs_hosting else "[green]no (resumable)[/]")
    console.print(t)
    db.close()


@app.command("token-status")
def token_status(page: str = typer.Option(...)):
    """Token identity, and — only with the app credentials — expiry and scopes.

    Two calls, because they answer different questions and need different
    credentials: ``/me`` says whether the token works and needs nothing else,
    ``/debug_token`` says how long it lives and needs META_APP_ID and
    META_APP_SECRET (see ``core.meta_api`` for why).
    """
    from ..publishing.health import app_credentials

    _, s, _, pcfg, db = _ctx(page)
    target = build_publish_target(s, pcfg)
    if not target:
        console.print("[red]Credenziali mancanti[/] (imposta le variabili in .env).")
        raise typer.Exit(1)
    try:
        console.print({"identità": target.client.verify_token()})
    except PublishError as e:
        console.print(f"[red]Token rifiutato o non verificabile:[/] {e}")
        db.close()
        raise typer.Exit(1)
    app_id, app_secret = app_credentials()
    if not (app_id and app_secret):
        console.print("[yellow]Scadenza e scope non verificabili[/]: servono "
                      "META_APP_ID e META_APP_SECRET (solo per questo).")
        db.close()
        raise typer.Exit(2)
    try:
        data = (target.client.debug_token(app_id, app_secret) or {}).get("data", {})
    except PublishError as e:
        console.print(f"[yellow]debug_token non disponibile:[/] {e}")
        db.close()
        raise typer.Exit(2)
    console.print({"is_valid": data.get("is_valid"),
                   "expires_at": data.get("expires_at"),
                   "scopes": data.get("scopes")})
    db.close()


@app.command("account-status")
def account_status(page: str = typer.Option(...)):
    """Check account type / id / publishing limit (fails if incompatible)."""
    _, s, _, pcfg, db = _ctx(page)
    target = build_publish_target(s, pcfg)
    if not target:
        console.print("[red]Credenziali mancanti[/].")
        raise typer.Exit(1)
    try:
        info = target.client.get_account_info(target.ig_user_id)
    except PublishError as e:
        console.print(f"[red]Errore account:[/] {e}")
        raise typer.Exit(1)
    atype = (info.get("account_type") or "").lower()
    console.print({"id": target.ig_user_id, "username": info.get("username"),
                   "account_type": atype or "?"})
    try:
        console.print("publishing_limit:", target.client.get_publishing_limit(target.ig_user_id))
    except PublishError as e:
        console.print(f"[yellow]limit non disponibile:[/] {e}")
    if atype == "personal":
        console.print("[red]Account personale: pubblicazione via API non supportata.[/]")
        raise typer.Exit(2)
    if atype and atype != "business":
        console.print("[yellow]Le Stories via API richiedono un account business.[/]")
    db.close()


@app.command("health-check")
def health_check(
    page: str = typer.Option(None, help="page_id (omesso con --all)"),
    all_pages: bool = typer.Option(False, "--all", help="controlla tutte le pagine"),
    warn_days: int = typer.Option(10, help="preavviso scadenza token, in giorni"),
):
    """Credential health for one or every page — never publishes, never prints a token.

    Five independent verdicts per page (token, expiry, permissions, account,
    limit), because "non ho potuto verificare" and "Meta dice di no" call for
    different actions. The logic lives in ``publishing.health``; this only
    renders it.

    Exit code: 0 everything green, 1 at least one failure, 2 only warnings.
    The first go-live treats 2 as blocking — see ``scripts/preflight.ps1``.
    """
    from ..publishing.health import Verdict, check_pages

    paths = Paths.create()
    settings = load_settings(paths)
    setup_logging(paths.logs, settings.logging.level, console=False)
    reg = load_pages(paths)
    if not all_pages and not page:
        console.print("[red]Serve --page <id> oppure --all[/]")
        raise typer.Exit(1)
    pages = reg.all() if all_pages else [reg.get(page)]

    report = check_pages(settings, pages, warn_days=warn_days)

    def paint(value: str, good: tuple[str, ...]) -> str:
        if value in good:
            return f"[green]{value}[/]"
        if value in (Verdict.INVALID, Verdict.EXPIRED, Verdict.MISSING,
                     Verdict.UNREACHABLE, Verdict.ABSENT):
            return f"[red]{value}[/]"
        return f"[yellow]{value}[/]"

    t = Table(title="Instagram — health check")
    for col in ("pagina", "credenziali", "token", "scadenza", "permessi",
                "account", "tipo", "limite 24h"):
        t.add_column(col)
    for h in report.pages:
        expiry = h.expiry if h.days_left is None else f"{h.expiry} ({h.days_left} gg)"
        t.add_row(h.page_id,
                  paint(h.credentials, (Verdict.OK,)),
                  paint(h.token, (Verdict.VALID,)),
                  paint(expiry, (Verdict.OK,)),
                  paint(h.permissions, (Verdict.OK,)),
                  paint(h.account, (Verdict.OK,)),
                  h.account_type or "—", h.limit)
    console.print(t)

    for h in report.pages:
        for problem in h.failures:
            console.print(f"  [red]{h.page_id}[/]: {problem}")
        for problem in h.warnings:
            console.print(f"  [yellow]{h.page_id}[/]: {problem}")
        for note in h.notes:
            console.print(f"  [dim]{h.page_id}: {note}[/]")
        # Where each verdict came from. A date somebody typed and a date Meta
        # returned are both "59 giorni" on the table, and the difference is
        # the whole reason to print this line.
        if h.expiry_source:
            console.print(f"  [dim]{h.page_id}: scadenza {h.expiry_source}[/]")
        if h.permissions_source:
            console.print(f"  [dim]{h.page_id}: permessi da {h.permissions_source}[/]")
    console.print("[dim]Nessun token e nessun app secret viene stampato o "
                  "registrato.[/]")
    raise typer.Exit(report.exit_code)


@app.command("upload-test")
def upload_test(page: str = typer.Option(...), file: str = typer.Option(...),
                media_type: str = typer.Option("REELS", help="REELS|STORIES"),
                publish: bool = typer.Option(False, "--publish/--no-publish",
                                             help="default: stop before publishing"),
                confirm: bool = typer.Option(
                    False, "--confirm",
                    help="richiesto quando ICE_MODE non è dry_run")):
    """Validate a file, create a resumable container, upload it, poll — WITHOUT
    publishing (unless --publish is explicitly given).

    Two independent guards stand between this command and a real post:
    ``--publish`` must be passed, and outside ``dry_run`` ``--confirm`` must be
    passed too. Neither is a default, and ``upload_would_publish()`` — the single
    function that decides — is covered by tests, so a refactor cannot quietly
    turn the safe path into the publishing one.
    """
    _, s, _, pcfg, db = _ctx(page)
    fpath = Path(file)
    if not fpath.exists():
        console.print(f"[red]File non trovato:[/] {file}")
        raise typer.Exit(1)

    allowed, reason = upload_would_publish(s.mode, publish=publish, confirm=confirm)
    if publish and not allowed:
        console.print(f"[red]Rifiuto di pubblicare:[/] {reason}")
        raise typer.Exit(2)
    log.info("upload-test page=%s file=%s media_type=%s mode=%s publish=%s",
             page, fpath.name, media_type, s.mode, allowed)
    target = build_publish_target(s, pcfg)
    if not target:
        console.print("[red]Credenziali mancanti[/].")
        raise typer.Exit(1)
    client = target.client
    size = fpath.stat().st_size
    console.print(f"file: {file} ({size} bytes), media_type={media_type}")
    try:
        cid, uri = client.create_resumable_container(
            target.ig_user_id, media_type=media_type,
            caption=("upload-test" if media_type == "REELS" else None),
            share_to_feed=(pcfg.publishing.share_reel_to_feed if media_type == "REELS" else None))
        console.print(f"[green]container:[/] {cid}")
        client.upload_video_file(uri, str(fpath), offset=0,
                                 timeout=s.publishing.upload_timeout_seconds)
        console.print("[green]upload:[/] ok")
        st = client.get_upload_status(cid)
        console.print("status:", st)
        if allowed:
            console.print("[yellow]--publish e --confirm passati: pubblico...[/]")
            mid = client.publish_container(target.ig_user_id, cid)
            console.print(f"[green]PUBLISHED media_id:[/] {mid}")
        else:
            console.print(f"[bold]Stop prima della pubblicazione[/]: {reason}")
    except PublishError as e:
        console.print(f"[red]Errore:[/] {e}")
        raise typer.Exit(1)
    db.close()


@app.command("create-container")
def create_container(page: str = typer.Option(...), job: int = typer.Option(...)):
    """Create (only) the resumable container for a job and persist it."""
    _, s, _, pcfg, db = _ctx(page)
    j = db.get_job(job)
    if not j:
        console.print(f"[red]Job {job} non trovato[/]")
        raise typer.Exit(1)
    if j.get("container_id"):
        console.print(f"[yellow]Container già presente:[/] {j['container_id']}")
        raise typer.Exit(0)
    target = build_publish_target(s, pcfg)
    if not target:
        console.print("[red]Credenziali mancanti[/].")
        raise typer.Exit(1)
    mt = MediaType(j["media_type"])
    meta_type = "REELS" if mt == MediaType.REEL else "STORIES"
    is_reel = mt == MediaType.REEL
    from ..content.captions import build_caption
    caption = None
    if is_reel and j.get("content_id"):
        c = db.get_content(j["content_id"])
        caption = build_caption(c, content_type=pcfg.content_type) if c else None
    try:
        cid, uri = target.client.create_resumable_container(
            target.ig_user_id, media_type=meta_type, caption=caption,
            share_to_feed=(pcfg.publishing.share_reel_to_feed if is_reel else None))
    except PublishError as e:
        console.print(f"[red]Errore:[/] {e}")
        raise typer.Exit(1)
    db.update_job(job, container_id=cid, upload_uri=uri,
                  status=JobStatus.CONTAINER_CREATED, upload_status=UploadStatus.PENDING)
    console.print(f"[green]container:[/] {cid}")
    db.close()


# =========================================================================
# The canary: three explicitly separated levels.
#
#   canary-plan     nothing leaves this machine
#   canary-upload   a real container and real bytes, and no media_publish
#   publish-canary  one media_publish on the container canary-upload left
#
# They are separate commands rather than flags on one command because the
# difference between them is the difference between "no network" and "a post on
# a real account", and that is not a difference to express with a switch.
# =========================================================================
def _canary_ctx(page_id: str):
    paths, settings, reg, pcfg, db = _ctx(page_id)
    from ..publishing import canary
    return paths, settings, reg, pcfg, db, canary


def _audit(path: str, settings) -> tuple[bool, list[str]]:
    from ..video.media_audit import probe_file

    check = probe_file(path, page_id="canary",
                       ffmpeg_path=settings.video.ffmpeg_path)
    return check.ok, list(check.problems)


def _caption_for(db, job: dict, pcfg) -> str:
    from ..content.captions import build_caption

    if not job.get("content_id"):
        return ""
    content = db.get_content(job["content_id"])
    return build_caption(content, content_type=pcfg.content_type) if content else ""


def _resolve_job(db, canary, pcfg, job_id: int | None) -> dict | None:
    if job_id:
        return db.get_job(job_id)
    return canary.select_job(db, pcfg.page_id)


@app.command("canary-plan")
def canary_plan(page: str = typer.Option(...),
                job: int = typer.Option(None, help="job esplicito; default: il primo futuro"),
                as_json: bool = typer.Option(False, "--json")):
    """Level 1 — everything the canary would do, with zero Meta calls.

    Chooses the job, probes the file against the published Reel specifications,
    builds the caption and names the target account. It does not construct a
    Graph client, so there is nothing here that *could* reach the network; a
    test blocks the HTTP layer outright and runs this command to prove it.
    """
    import json as _json

    _, s, _, pcfg, db, canary = _canary_ctx(page)
    j = _resolve_job(db, canary, pcfg, job)
    if not j:
        console.print(f"[red]Nessun job futuro con media pronto per {page}.[/]")
        console.print("Prepara il buffer dalla data odierna:")
        console.print("  python -m src.cli prepare-buffer --from <YYYY-MM-DD> "
                      "--days 30 --all-pages --require-comfyui")
        db.close()
        raise typer.Exit(1)

    audit_ok, problems = _audit(j["output_path"], s)
    caption = _caption_for(db, j, pcfg)
    prefix = pcfg.env_prefix()
    uid = os.environ.get(f"{prefix}_IG_USER_ID") or ""
    has_token = bool(os.environ.get(f"{prefix}_ACCESS_TOKEN")
                     or os.environ.get("META_ACCESS_TOKEN"))
    state = canary.load_state(db, pcfg.page_id)

    if as_json:
        # typer.echo, not console.print_json: this output is parsed by
        # go_live_canary.ps1 and rich would decorate it.
        typer.echo(_json.dumps({
            "page_id": pcfg.page_id, "job_id": j["id"],
            "scheduled_at": j.get("scheduled_at"),
            "output_path": j.get("output_path"), "audit_ok": audit_ok,
            "problems": problems, "caption": caption,
            "ig_user_id_set": bool(uid), "access_token_set": has_token,
            "container_id": state.container_id,
            "already_uploaded": bool(state.container_id and state.job_id == j["id"]),
        }))
        db.close()
        raise typer.Exit(0 if audit_ok else 1)

    t = Table(title=f"Canary — simulazione senza rete ({pcfg.page_id})")
    t.add_column("voce"); t.add_column("valore")
    t.add_row("job", str(j["id"]))
    t.add_row("programmato", str(j.get("scheduled_at")))
    t.add_row("file", str(j.get("output_path")))
    t.add_row("specifiche Meta", "[green]conformi[/]" if audit_ok else "[red]NON conformi[/]")
    t.add_row("account (IG_USER_ID)", "[green]impostato[/]" if uid else "[red]assente[/]")
    t.add_row("token", "[green]impostato[/]" if has_token else "[red]assente[/]")
    t.add_row("container già caricato", state.container_id or "nessuno")
    console.print(t)
    for p in problems:
        console.print(f"  [red]{p}[/]")
    console.print("\n[yellow]Didascalia che verrebbe pubblicata:[/]")
    console.print(caption or "[dim](nessuna)[/]")
    console.print("\n[bold]Passi simulati:[/] create container -> resumable upload -> "
                  "attesa FINISHED -> conferma -> media_publish")
    console.print("[green]Nessuna chiamata a Meta è stata effettuata: 0 container, "
                  "0 upload, 0 publish.[/]")
    db.close()
    raise typer.Exit(0 if audit_ok else 1)


@app.command("canary-upload")
def canary_upload(page: str = typer.Option(...),
                  job: int = typer.Option(None,
                                          help="job esplicito; default: il primo futuro")):
    """Level 2 — a real container and a real upload, and no publication.

    Ends with the container sitting on Meta in FINISHED, recorded against the
    job. Nothing is visible on the account.
    """
    _, s, _, pcfg, db, canary = _canary_ctx(page)
    if s.mode == Mode.DRY_RUN:
        console.print("[red]ICE_MODE=dry_run[/]: nessuna chiamata reale. "
                      "Il canary imposta la modalità da sé; non modificare .env.")
        db.close()
        raise typer.Exit(2)

    from ..publishing.health import check_page
    health = check_page(s, pcfg)
    if health.failures or health.warnings:
        for problem in health.failures + health.warnings:
            console.print(f"  [red]{problem}[/]")
        console.print("[red]Health check non verde: non carico nulla.[/]")
        db.close()
        raise typer.Exit(1)

    j = _resolve_job(db, canary, pcfg, job)
    if not j:
        console.print(f"[red]Nessun job futuro con media pronto per {page}.[/]")
        db.close()
        raise typer.Exit(1)
    audit_ok, problems = _audit(j["output_path"], s)
    if not audit_ok:
        for p in problems:
            console.print(f"  [red]{p}[/]")
        db.close()
        raise typer.Exit(1)

    target = build_publish_target(s, pcfg)
    if not target:
        console.print("[red]Credenziali mancanti[/].")
        db.close()
        raise typer.Exit(1)

    def poll(container_id: str) -> None:
        import time
        waited = 0.0
        interval = s.publishing.status_poll_interval_seconds
        while waited <= s.publishing.status_poll_max_seconds:
            status = target.client.get_upload_status(container_id).get("status_code")
            console.print(f"  [dim]container {container_id}: {status}[/]")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(f"container {status}", retryable=False, code=status)
            time.sleep(interval)
            waited += interval
        raise PublishError("timeout in attesa di FINISHED", retryable=True)

    try:
        state = canary.prepare_upload(
            db, target=target, page=pcfg, job=j,
            caption=_caption_for(db, j, pcfg),
            share_to_feed=pcfg.publishing.share_reel_to_feed, poll=poll)
    except PublishError as e:
        console.print(f"[red]Upload non riuscito:[/] {e}")
        console.print("[green]MEDIA NON PUBBLICATO[/]")
        db.close()
        raise typer.Exit(1)

    console.print(f"\n[bold green]UPLOAD META RIUSCITO[/]  container "
                  f"{state.container_id}  job {j['id']}")
    console.print("[bold green]MEDIA NON PUBBLICATO[/]")
    console.print("Per pubblicarlo:  .\\scripts\\go_live_canary.ps1")
    db.close()


@app.command("canary-status")
def canary_status(page: str = typer.Option(...)):
    """What the canary has already done, so nothing is repeated by accident."""
    _, _, _, pcfg, db, canary = _canary_ctx(page)
    state = canary.load_state(db, pcfg.page_id)
    used = canary.published_record(db)
    attempted = canary.attempt_record(db)
    t = Table(title=f"Canary — {pcfg.page_id}")
    t.add_column("voce"); t.add_column("valore")
    t.add_row("container caricato", state.container_id or "nessuno")
    t.add_row("job associato", str(state.job_id) if state.job_id else "—")
    t.add_row("caricato il", state.uploaded_at or "—")
    t.add_row("tentativo registrato", (attempted or {}).get("attempted_at", "—"))
    t.add_row("già pubblicato", (used or {}).get("published_at", "no"))
    t.add_row("media id", (used or {}).get("media_id", "—"))
    console.print(t)
    db.close()
    raise typer.Exit(0 if used is None else 3)


@app.command("publish-canary")
def publish_canary_cmd(
    page: str = typer.Option(..., help="una sola pagina, esplicita"),
    job: int = typer.Option(..., help="un solo job, esplicito"),
    confirm: str = typer.Option("", "--confirm",
                                help="deve valere esattamente PUBBLICA"),
):
    """Level 3 — one ``media_publish``, on the container ``canary-upload`` left.

    This is the only path in the project that can publish without the page
    being armed, and it stays impossible to reach by accident: an explicit page,
    an explicit job, a container that already exists and is FINISHED, a green
    audit, a green health check, a terminal, the word, and never twice.

    It does not arm the page. Arming is a separate decision, taken after you
    have seen the post.
    """
    import sys

    _, s, _, pcfg, db, canary = _canary_ctx(page)
    if s.mode == Mode.DRY_RUN:
        console.print("[red]ICE_MODE=dry_run[/]: nessuna pubblicazione reale.")
        db.close()
        raise typer.Exit(2)

    from ..publishing.health import check_page
    health = check_page(s, pcfg)
    health_ok = not (health.failures or health.warnings)
    for problem in health.failures + health.warnings:
        console.print(f"  [yellow]{problem}[/]")

    j = db.get_job(job)
    audit_ok = False
    if j and j.get("output_path"):
        audit_ok, problems = _audit(j["output_path"], s)
        for p in problems:
            console.print(f"  [red]{p}[/]")

    target = build_publish_target(s, pcfg)
    if not target:
        console.print("[red]Credenziali mancanti[/].")
        db.close()
        raise typer.Exit(1)

    outcome = canary.publish_canary(
        db, target=target, page_id=pcfg.page_id, job_id=job, confirmation=confirm,
        interactive=sys.stdin.isatty(), audit_ok=audit_ok, health_ok=health_ok)

    if not outcome.published:
        console.print(f"[red]Canary rifiutato:[/] {outcome.reason}")
        console.print("[green]Nessuna pubblicazione effettuata.[/]")
        db.close()
        raise typer.Exit(1)

    console.print(f"[bold green]PUBBLICATO[/] media_id={outcome.media_id} "
                  f"(container {outcome.container_id})")
    console.print("[yellow]La pagina resta DISARMATA[/]: il worker non "
                  "pubblicherà nulla finché non esegui arm_page.ps1.")
    db.close()


@app.command("publish-job")
def publish_job(page: str = typer.Option(...), job: int = typer.Option(...),
                confirm: bool = typer.Option(False, "--confirm",
                                             help="required to actually publish")):
    """Run the full resumable publish for a job. Requires --confirm."""
    _, s, reg, pcfg, db = _ctx(page)
    j = db.get_job(job)
    if not j:
        console.print(f"[red]Job {job} non trovato[/]")
        raise typer.Exit(1)
    if not confirm:
        console.print("[yellow]Aggiungi --confirm per pubblicare davvero.[/] Nessuna azione.")
        raise typer.Exit(0)
    if s.mode == Mode.DRY_RUN:
        console.print("[yellow]Mode dry_run: nessuna pubblicazione reale (imposta ICE_MODE=test|production).[/]")
    pub = Publisher(s, db, reg)
    outcome = pub.publish_job(job)
    console.print(f"job {job}: [bold]{outcome.status}[/] — {outcome.message}")
    if outcome.remote_media_id:
        console.print(f"media_id: {outcome.remote_media_id}")
    db.close()
