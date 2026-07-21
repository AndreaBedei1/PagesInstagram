"""Minimal local review dashboard.

Security: binds to 127.0.0.1 by default, requires a token (ICE_DASHBOARD_TOKEN)
on every request, serves media files only from within ``generated/`` (no path
traversal). Not intended to be exposed publicly.
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..accounts import load_pages
from ..core.enums import ContentStatus
from ..core.paths import Paths
from ..core.settings import Settings, load_settings
from ..database import Database
from ..monitoring import status_summary


def _token(settings: Settings) -> str:
    return os.environ.get("ICE_DASHBOARD_TOKEN") or "local"


def create_app(settings: Settings | None = None) -> FastAPI:
    paths = Paths.create()
    settings = settings or load_settings(paths)
    app = FastAPI(title="Instagram Content Engine — Dashboard")
    token = _token(settings)

    # register pages so pause/resume has rows to update
    reg = load_pages(paths)
    with Database.open(settings.db_path()) as db:
        for p in reg.all():
            if db.get_page(p.page_id) is None:
                db.upsert_page(p.page_id, p.display_name, p.content_type,
                               p.enabled, p.config_path)

    def get_db():
        db = Database.open(settings.db_path())
        try:
            yield db
        finally:
            db.close()

    def check(request: Request):
        if request.query_params.get("token") != token:
            raise HTTPException(status_code=403, detail="token mancante o errato (?token=...)")

    def link(path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}token={token}"

    def page(title: str, body: str) -> HTMLResponse:
        nav = " · ".join(
            f'<a href="{link(p)}">{n}</a>'
            for p, n in [("/", "Panoramica"), ("/review", "Revisione"),
                         ("/media", "Media"), ("/jobs", "Job")]
        )
        return HTMLResponse(f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
        <style>
          body{{font-family:system-ui,Segoe UI,Arial;margin:0;background:#0f1115;color:#e8e8ea}}
          header{{padding:14px 22px;background:#171a21;position:sticky;top:0;display:flex;gap:16px;align-items:center}}
          header a{{color:#8ab4ff;text-decoration:none}} h1{{font-size:16px;margin:0 16px 0 0}}
          main{{padding:22px;max-width:1100px;margin:auto}}
          table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #262a33;padding:8px;text-align:left;font-size:14px}}
          .card{{background:#171a21;border:1px solid #262a33;border-radius:10px;padding:14px;margin-bottom:14px}}
          .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}}
          img,video{{width:100%;border-radius:8px;background:#000}}
          button{{background:#2b62d6;color:#fff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer}}
          button.warn{{background:#a13a3a}} .muted{{color:#9aa0aa;font-size:13px}}
          form{{display:inline}}
        </style></head><body>
        <header><h1>ICE Dashboard</h1>{nav}<span class="muted" style="margin-left:auto">mode: {settings.mode}</span></header>
        <main>{body}</main></body></html>""")

    # -- routes ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, db: Database = Depends(get_db)):
        check(request)
        s = status_summary(db)
        pages_rows = "".join(
            f"<tr><td>{html.escape(p['page_id'])}</td><td>{html.escape(p['content_type'])}</td>"
            f"<td>{'attiva' if not db.is_page_paused(p['page_id']) else '<b>in pausa</b>'}</td>"
            f"<td><form method='post' action='{link('/page/' + p['page_id'] + '/toggle')}'>"
            f"<button>{'Pausa' if not db.is_page_paused(p['page_id']) else 'Riattiva'}</button></form></td></tr>"
            for p in db.list_pages()
        )
        cj = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(s["jobs_by_status"].items()))
        cc = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(s["contents_by_status"].items()))
        upcoming = "".join(
            f"<tr><td>{html.escape(j['page_id'])}</td><td>{j['media_type']}</td>"
            f"<td>{j['status']}</td><td>{j['scheduled_at']}</td></tr>" for j in s["upcoming_jobs"])
        body = f"""
        <div class="card"><h3>Pagine</h3><table><tr><th>page</th><th>tipo</th><th>stato</th><th></th></tr>{pages_rows}</table></div>
        <div class="grid">
          <div class="card"><h3>Contenuti</h3><table>{cc}</table></div>
          <div class="card"><h3>Job</h3><table>{cj}</table></div>
        </div>
        <div class="card"><h3>Prossimi job</h3><table><tr><th>page</th><th>media</th><th>stato</th><th>quando</th></tr>{upcoming}</table></div>
        <div class="card"><h3>Azioni</h3>
          <form method="post" action="{link('/actions/retry-failed')}"><button>Riprova falliti</button></form>
          <form method="post" action="{link('/actions/publish-next')}"><button>Publish next (dry-run)</button></form>
        </div>"""
        return page("Panoramica", body)

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, db: Database = Depends(get_db)):
        check(request)
        rows = db.conn.execute(
            "SELECT id, content_type, author, text, quality_score, status, "
            "attribution_confidence FROM contents "
            "WHERE status IN ('needs_review','approved_for_publication') "
            "ORDER BY (status='needs_review') DESC, id DESC LIMIT 200"
        ).fetchall()
        items = ""
        for r in rows:
            actions = (
                f"<form method='post' action='{link('/content/' + str(r['id']) + '/approve')}'><button>Approva</button></form> "
                f"<form method='post' action='{link('/content/' + str(r['id']) + '/reject')}'><button class='warn'>Rifiuta</button></form>"
            )
            items += (
                f"<tr><td>{r['id']}</td><td>{html.escape(r['content_type'])}</td>"
                f"<td>{html.escape(r['author'] or '')}</td>"
                f"<td>{html.escape((r['text'] or '')[:90])}</td>"
                f"<td>{r['quality_score']}</td>"
                f"<td>{html.escape(r['status'])} {html.escape(r['attribution_confidence'] or '')}</td>"
                f"<td>{actions}</td></tr>"
            )
        return page("Revisione", f"<div class='card'><table>"
                    f"<tr><th>id</th><th>tipo</th><th>autore</th><th>testo</th><th>q</th><th>stato</th><th></th></tr>"
                    f"{items}</table></div>")

    @app.get("/media", response_class=HTMLResponse)
    def media(request: Request, db: Database = Depends(get_db)):
        check(request)
        rows = db.conn.execute(
            "SELECT m.*, c.text AS text, c.author AS author FROM media_assets m "
            "JOIN contents c ON c.id=m.content_id ORDER BY m.id DESC LIMIT 40"
        ).fetchall()
        cards = ""
        for r in rows:
            def flink(p):
                if not p:
                    return ""
                try:
                    rel = Path(p).resolve().relative_to(paths.generated.resolve()).as_posix()
                except ValueError:
                    return ""
                return link(f"/file?path={rel}")
            vids = "".join(f'<video src="{flink(v)}" controls preload="none"></video>'
                           for v in (r["post_video_path"], r["story_video_path"]) if flink(v))
            imgs = "".join(f'<img src="{flink(i)}" loading="lazy">'
                           for i in (r["post_image_path"], r["story_image_path"]) if flink(i))
            cards += (f"<div class='card'>{imgs}{vids}"
                      f"<p class='muted'>{html.escape((r['text'] or '')[:80])}</p></div>")
        return page("Media", f"<div class='grid'>{cards or '<p>Nessun media.</p>'}</div>")

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, db: Database = Depends(get_db)):
        check(request)
        rows = db.list_jobs()
        body = "<div class='card'><table><tr><th>id</th><th>page</th><th>media</th><th>stato</th>" \
               "<th>quando</th><th>retry</th><th>errore</th></tr>"
        for j in rows[:200]:
            body += (f"<tr><td>{j['id']}</td><td>{html.escape(j['page_id'])}</td>"
                     f"<td>{j['media_type']}</td><td>{j['status']}</td>"
                     f"<td>{j['scheduled_at'] or ''}</td><td>{j['retry_count']}</td>"
                     f"<td class='muted'>{html.escape((j['last_error'] or '')[:80])}</td></tr>")
        return page("Job", body + "</table></div>")

    @app.get("/file")
    def serve_file(request: Request, path: str):
        check(request)
        target = (paths.generated / path).resolve()
        if not str(target).startswith(str(paths.generated.resolve())) or not target.exists():
            raise HTTPException(404)
        return FileResponse(target)

    @app.post("/content/{cid}/{action}")
    def content_action(cid: int, action: str, request: Request, db: Database = Depends(get_db)):
        check(request)
        if action == "approve":
            db.update_content(cid, status=ContentStatus.APPROVED_FOR_PUBLICATION)
        elif action == "reject":
            db.update_content(cid, status=ContentStatus.REJECTED)
        return RedirectResponse(link("/review"), status_code=303)

    @app.post("/page/{pid}/toggle")
    def toggle_page(pid: str, request: Request, db: Database = Depends(get_db)):
        check(request)
        db.set_page_enabled(pid, db.is_page_paused(pid))  # flip
        return RedirectResponse(link("/"), status_code=303)

    @app.post("/actions/retry-failed")
    def retry_failed(request: Request, db: Database = Depends(get_db)):
        check(request)
        from ..core.enums import JobStatus
        from ..core.timeutils import utcnow_iso
        for j in db.list_jobs(status=JobStatus.FAILED):
            db.update_job(j["id"], status=JobStatus.RETRY_PENDING, next_retry_at=utcnow_iso())
        return RedirectResponse(link("/"), status_code=303)

    @app.post("/actions/publish-next")
    def publish_next(request: Request):
        check(request)
        # Run a single dry-run tick in a fresh context (keeps the request quick-ish).
        from ..scheduling.worker import TickStats, Worker
        s2 = load_settings(paths)
        s2.mode = s2.mode  # keep configured mode; dashboard button is informational
        db2 = Database.open(s2.db_path())
        try:
            w = Worker(s2, db2, reg)
            w._prepare_media(TickStats())
        finally:
            db2.close()
        return RedirectResponse(link("/"), status_code=303)

    return app


def run_dashboard(settings: Settings | None = None, *, host: str | None = None,
                  port: int | None = None) -> None:
    import uvicorn

    settings = settings or load_settings()
    host = host or settings.dashboard.host
    port = port or settings.dashboard.port
    token = _token(settings)
    print(f"Dashboard: http://{host}:{port}/?token={token}")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
