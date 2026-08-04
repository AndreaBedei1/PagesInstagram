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
from ..core.logging_setup import setup_logging
from ..core.paths import Paths
from ..core.settings import load_settings
from ..core.timeutils import utcnow_iso
from ..database import Database
from ..publishing import Publisher, build_publish_target

app = typer.Typer(help="Meta account, token and direct resumable-upload commands",
                  no_args_is_help=True)
console = Console()


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
    """Check the access token validity (debug_token)."""
    _, s, _, pcfg, db = _ctx(page)
    target = build_publish_target(s, pcfg)
    if not target:
        console.print("[red]Credenziali mancanti[/] (imposta le variabili in .env).")
        raise typer.Exit(1)
    info = target.client.debug_token()
    data = info.get("data", info)
    console.print({"is_valid": data.get("is_valid"), "expires_at": data.get("expires_at"),
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

    Checks, per page: credentials present, token valid, known expiry (with an
    advance warning), account reachable, account type, publishing limit and the
    publishing configuration. Exit code: 0 all green, 1 at least one failure,
    2 only warnings.
    """
    from datetime import datetime, timezone

    paths = Paths.create()
    settings = load_settings(paths)
    setup_logging(paths.logs, settings.logging.level, console=False)
    reg = load_pages(paths)
    if not all_pages and not page:
        console.print("[red]Serve --page <id> oppure --all[/]")
        raise typer.Exit(1)
    pages = reg.all() if all_pages else [reg.get(page)]

    t = Table(title="Instagram — health check")
    for col in ("pagina", "credenziali", "token", "scadenza", "account",
                "tipo", "limite 24h", "upload"):
        t.add_column(col)

    failures = warnings = 0
    for pcfg in pages:
        prefix = pcfg.env_prefix()
        has_uid = bool(os.environ.get(f"{prefix}_IG_USER_ID"))
        has_tok = bool(os.environ.get(f"{prefix}_ACCESS_TOKEN")
                       or os.environ.get("META_ACCESS_TOKEN"))
        creds = "[green]ok[/]" if (has_uid and has_tok) else "[yellow]mancanti[/]"
        token_col = expiry_col = account_col = type_col = limit_col = "—"
        upload_col = pcfg.publishing.upload_method

        target = build_publish_target(settings, pcfg) if (has_uid and has_tok) else None
        if target is None:
            warnings += 1
        else:
            try:
                data = (target.client.debug_token() or {}).get("data", {})
                valid = bool(data.get("is_valid"))
                token_col = "[green]valido[/]" if valid else "[red]non valido[/]"
                if not valid:
                    failures += 1
                exp = data.get("expires_at")
                if exp in (0, None):
                    expiry_col = "senza scadenza nota"
                else:
                    dt = datetime.fromtimestamp(int(exp), tz=timezone.utc)
                    left = (dt - datetime.now(timezone.utc)).days
                    expiry_col = f"{dt.date().isoformat()} ({left} gg)"
                    if left < 0:
                        expiry_col = f"[red]{expiry_col}[/]"
                        failures += 1
                    elif left <= warn_days:
                        expiry_col = f"[yellow]{expiry_col}[/]"
                        warnings += 1
            except PublishError as e:
                token_col = "[red]errore[/]"
                console.print(f"[red]{pcfg.page_id} token:[/] {e}")
                failures += 1
            try:
                info = target.client.get_account_info(target.ig_user_id)
                account_col = f"[green]{info.get('username') or 'ok'}[/]"
                atype = (info.get("account_type") or "?").lower()
                type_col = atype
                if atype == "personal":
                    type_col = "[red]personal[/]"
                    failures += 1
                elif atype and atype != "business":
                    type_col = f"[yellow]{atype}[/]"
                    warnings += 1
            except PublishError as e:
                account_col = "[red]irraggiungibile[/]"
                console.print(f"[red]{pcfg.page_id} account:[/] {e}")
                failures += 1
            try:
                lim = target.client.get_publishing_limit(target.ig_user_id) or {}
                quota = (lim.get("data") or [{}])[0] if isinstance(lim.get("data"), list) else lim
                limit_col = str(quota.get("quota_usage", quota.get("config", "?")))
            except PublishError:
                limit_col = "[yellow]n/d[/]"

        if pcfg.publishing.upload_method == "hosted_url":
            upload_col = "[yellow]hosted_url (serve hosting)[/]"
            warnings += 1
        else:
            upload_col = "[green]resumable (nessun hosting)[/]"

        t.add_row(pcfg.page_id, creds, token_col, expiry_col, account_col,
                  type_col, limit_col, upload_col)

    console.print(t)
    console.print("[dim]Nessun token viene mai stampato o registrato.[/]")
    if failures:
        raise typer.Exit(1)
    raise typer.Exit(2 if warnings else 0)


@app.command("upload-test")
def upload_test(page: str = typer.Option(...), file: str = typer.Option(...),
                media_type: str = typer.Option("REELS", help="REELS|STORIES"),
                publish: bool = typer.Option(False, "--publish/--no-publish",
                                             help="default: stop before publishing")):
    """Validate a file, create a resumable container, upload it, poll — WITHOUT
    publishing (unless --publish is explicitly given)."""
    _, s, _, pcfg, db = _ctx(page)
    fpath = Path(file)
    if not fpath.exists():
        console.print(f"[red]File non trovato:[/] {file}")
        raise typer.Exit(1)
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
        if publish:
            console.print("[yellow]--publish passato: pubblico...[/]")
            mid = client.publish_container(target.ig_user_id, cid)
            console.print(f"[green]PUBLISHED media_id:[/] {mid}")
        else:
            console.print("[bold]Stop prima della pubblicazione[/] (usa 'publish-job --confirm').")
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
