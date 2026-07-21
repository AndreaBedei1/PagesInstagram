"""Status summaries, JSON reports and a self-contained HTML preview gallery."""
from __future__ import annotations

import html
import json
from pathlib import Path

from ..core.paths import Paths
from ..core.timeutils import utcnow_iso
from ..database import Database


def _counts(db: Database, table: str, column: str) -> dict[str, int]:
    rows = db.conn.execute(
        f"SELECT {column} AS k, COUNT(*) AS c FROM {table} GROUP BY {column}"
    ).fetchall()
    return {(r["k"] or "?"): r["c"] for r in rows}


def status_summary(db: Database) -> dict:
    upcoming = db.conn.execute(
        "SELECT page_id, media_type, status, scheduled_at FROM publication_jobs "
        "WHERE status NOT IN ('PUBLISHED','SKIPPED','REJECTED') "
        "AND scheduled_at IS NOT NULL ORDER BY scheduled_at LIMIT 10"
    ).fetchall()
    return {
        "generated_at": utcnow_iso(),
        "contents_by_status": _counts(db, "contents", "status"),
        "contents_by_type": _counts(db, "contents", "content_type"),
        "jobs_by_status": _counts(db, "publication_jobs", "status"),
        "music_tracks": db.conn.execute("SELECT COUNT(*) FROM music_tracks").fetchone()[0],
        "upcoming_jobs": [dict(r) for r in upcoming],
    }


def export_report(db: Database, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = status_summary(db)
    report["recent_jobs"] = [
        dict(r) for r in db.conn.execute(
            "SELECT id, page_id, media_type, status, scheduled_at, published_at, "
            "retry_count, last_error, remote_media_id FROM publication_jobs "
            "ORDER BY id DESC LIMIT 100"
        ).fetchall()
    ]
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _rel(path: str | None, base: Path) -> str | None:
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path


def build_preview_html(db: Database, paths: Paths, *, limit: int = 30,
                       out_path: Path | None = None) -> Path:
    """Write a self-contained gallery of recent media to generated/preview.html."""
    out_path = out_path or (paths.generated / "preview.html")
    rows = db.conn.execute(
        "SELECT m.*, c.text AS text, c.author AS author, c.caption AS caption, "
        "c.mood AS mood, c.content_type AS content_type, c.status AS cstatus "
        "FROM media_assets m JOIN contents c ON c.id=m.content_id "
        "ORDER BY m.id DESC LIMIT ?", (limit,)
    ).fetchall()

    cards = []
    for r in rows:
        post_img = _rel(r["post_image_path"], paths.generated)
        story_img = _rel(r["story_image_path"], paths.generated)
        post_vid = _rel(r["post_video_path"], paths.generated)
        story_vid = _rel(r["story_video_path"], paths.generated)
        meta = {}
        try:
            meta = json.loads(r["render_metadata"] or "{}")
        except (ValueError, TypeError):
            pass
        imgs = "".join(
            f'<img src="{html.escape(p)}" loading="lazy">' for p in (post_img, story_img) if p
        )
        vids = "".join(
            f'<video src="{html.escape(v)}" controls preload="none"></video>'
            for v in (post_vid, story_vid) if v
        )
        cards.append(f"""
        <div class="card">
          <div class="media">{imgs}{vids}</div>
          <div class="info">
            <div class="badge">{html.escape(r['content_type'] or '')} · {html.escape(r['mood'] or '')}
              · score {r['validation_score'] if r['validation_score'] is not None else '?'}</div>
            <p class="text">{html.escape(r['text'] or '')}</p>
            <p class="cap">{html.escape((r['caption'] or '')[:220])}</p>
            <p class="music">🎵 {html.escape(str(meta.get('music_track_id') or '—'))}</p>
          </div>
        </div>""")

    doc = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Instagram Content Engine — Preview</title>
    <style>
      body{{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#0f1115;color:#e8e8ea}}
      header{{padding:16px 24px;background:#171a21;position:sticky;top:0}}
      h1{{font-size:18px;margin:0}}
      .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:18px;padding:24px}}
      .card{{background:#171a21;border:1px solid #262a33;border-radius:12px;overflow:hidden}}
      .media{{display:flex;gap:6px;padding:8px;flex-wrap:wrap;background:#0c0e12}}
      .media img,.media video{{width:48%;border-radius:8px;background:#000}}
      .info{{padding:12px}}
      .badge{{font-size:12px;color:#9aa0aa;margin-bottom:6px}}
      .text{{font-weight:600;margin:6px 0}}
      .cap{{font-size:13px;color:#c2c6cd}}
      .music{{font-size:12px;color:#8a909a}}
    </style></head><body>
    <header><h1>Instagram Content Engine — Preview ({len(rows)} elementi)</h1></header>
    <div class="grid">{''.join(cards) or '<p style="padding:24px">Nessun media generato. Esegui: <code>python -m src.cli generate --page motivational_it --count 3</code></p>'}</div>
    </body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    return out_path
