"""Real per-page previews and a random content sample for human review.

``build_page_previews`` renders N genuine images per page through the exact
production path (background → template → quality auto-repair) and writes an HTML
index that puts the five styles side by side, so the layouts can be compared at a
glance rather than described.

``build_sample_review`` writes a second HTML sheet with a deterministic random
sample of the *text* of each page (≥25 items), including sources, so the dataset
itself can be reviewed without opening the JSON.
"""
from __future__ import annotations

import html
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..accounts.registry import AccountRegistry
from ..comfyui.backgrounds import BackgroundGenerator
from ..content.selection import background_seed, cycle_position, page_anchor
from ..core.settings import Settings
from ..database import Database
from ..quality.validator import MediaValidator
from ..rendering.renderer import Renderer
from ..rendering.templates import build_fields

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
       background:#0f1115; color:#e8e8ea; }
header { padding:28px 32px 8px; }
h1 { margin:0 0 6px; font-size:24px; letter-spacing:-0.01em; }
p.sub { margin:0; color:#9aa0aa; }
section { padding:20px 32px 8px; }
h2 { font-size:17px; margin:24px 0 4px; }
h2 small { font-weight:400; color:#9aa0aa; margin-left:8px; }
.row { display:flex; gap:14px; overflow-x:auto; padding:12px 0 18px; }
.card { flex:0 0 auto; width:210px; background:#171a21; border:1px solid #262b35;
        border-radius:12px; overflow:hidden; }
.card img { display:block; width:100%; height:auto; }
.card .meta { padding:8px 10px; font-size:12px; color:#9aa0aa; }
.badge { display:inline-block; padding:2px 8px; border-radius:99px; font-size:11px;
         background:#232833; color:#c8cdd6; margin-right:6px; }
table { border-collapse:collapse; width:100%; font-size:13.5px; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid #262b35;
        vertical-align:top; }
th { color:#9aa0aa; font-weight:600; }
td.text { max-width:520px; }
a { color:#7fb2ff; }
code { background:#232833; padding:1px 5px; border-radius:4px; }
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


@dataclass
class PreviewResult:
    index_path: Path
    images: dict[str, list[Path]] = field(default_factory=dict)


def _sample_contents(db: Database, content_type: str, n: int,
                     seed: int = 20260804) -> list[dict]:
    rows = db.list_contents(content_type=content_type,
                            status="approved_for_publication")
    if not rows:
        rows = db.list_contents(content_type=content_type)
    if len(rows) <= n:
        return rows
    rnd = random.Random(seed)
    return [rows[i] for i in sorted(rnd.sample(range(len(rows)), n))]


def build_page_previews(settings: Settings, db: Database, registry: AccountRegistry,
                        *, out_dir: str | Path, per_page: int = 5,
                        try_comfyui: bool = False) -> PreviewResult:
    out_dir = Path(out_dir)
    (out_dir / "img").mkdir(parents=True, exist_ok=True)
    bg = BackgroundGenerator(settings)
    renderer = Renderer(settings)
    validator = MediaValidator(settings)

    images: dict[str, list[Path]] = {}
    scores: dict[str, list[float]] = {}
    for page in registry.all():
        picks = _sample_contents(db, page.content_type, per_page)
        images[page.page_id] = []
        scores[page.page_id] = []
        for k, content in enumerate(picks):
            stem = f"{page.page_id}_{k}"
            seq = content.get("sequence_index")
            try:
                cycle = cycle_position(page_anchor(page), "2026-01-01").cycle_number
            except Exception:  # noqa: BLE001 — legacy pages have no anchor
                cycle = 0
            seed = background_seed(page_id=page.page_id,
                                   content_id=int(content["id"]),
                                   scheduled_date=str(seq), cycle_number=cycle,
                                   media_type="reel")
            bgres = bg.generate(
                out_path=out_dir / "img" / f"{stem}_bg.png",
                background_prompt=content.get("background_prompt"),
                mood=content.get("mood"), profile=page.visual.background_profile,
                aspect="reel", seed=seed, allow_fallback=True,
                try_comfyui=try_comfyui)
            fields = build_fields(page.template_id(), content,
                                  show_author=page.visual.show_author)
            target = out_dir / "img" / f"{stem}.png"

            def rf(opts, _bg=bgres.path, _t=target, _f=fields, _c=content, _p=page):
                return renderer.render(
                    background_path=_bg, out_path=_t, text=_c.get("text") or "",
                    content_type=_p.content_type, aspect="reel",
                    template_id=_p.template_id(), fields=_f,
                    logo_text=(_p.visual.logo_text if _p.visual.logo_enabled else None),
                    options=opts)

            res, val, _ = validator.render_until_valid(rf)
            images[page.page_id].append(Path(res.path))
            scores[page.page_id].append(val.score)

    index = _render_preview_index(registry, images, scores, out_dir,
                                  source="comfyui" if try_comfyui else "fallback")
    return PreviewResult(index_path=index, images=images)


def _render_preview_index(registry: AccountRegistry, images: dict[str, list[Path]],
                          scores: dict[str, list[float]], out_dir: Path,
                          source: str) -> Path:
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Anteprime — cinque pagine evergreen</title>",
        f"<style>{_CSS}</style>",
        "<header><h1>Anteprime delle cinque pagine</h1>",
        f"<p class='sub'>Rendering reale 1080×1920 · sfondi: <code>{_esc(source)}</code>"
        " · testo aggiunto con Pillow, mai dal modello generativo.</p></header>",
    ]
    for page in registry.all():
        imgs = images.get(page.page_id, [])
        sc = scores.get(page.page_id, [])
        avg = f"{sum(sc) / len(sc):.2f}" if sc else "—"
        parts.append(
            f"<section><h2>{_esc(page.display_name)}"
            f"<small>{_esc(page.page_id)} · template "
            f"<code>{_esc(page.template_id())}</code> · profilo sfondo "
            f"<code>{_esc(page.visual.background_profile)}</code> · "
            f"punteggio medio {avg}</small></h2><div class='row'>"
        )
        for img, s in zip(imgs, sc):
            rel = Path(img).relative_to(out_dir).as_posix()
            parts.append(
                f"<div class='card'><img loading='lazy' src='{_esc(rel)}' alt=''>"
                f"<div class='meta'><span class='badge'>score {s:.2f}</span>"
                f"{_esc(Path(img).name)}</div></div>")
        parts.append("</div></section>")
    out = out_dir / "index.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def build_sample_review(db: Database, registry: AccountRegistry,
                        out_path: str | Path, *, per_page: int = 25) -> Path:
    """HTML sheet with a random sample of each page's contents (text + sources)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Campione di revisione contenuti</title>",
        f"<style>{_CSS}</style>",
        "<header><h1>Campione di revisione</h1>",
        f"<p class='sub'>{per_page} contenuti estratti in modo deterministico "
        "per ciascuna pagina.</p></header>",
    ]
    for page in registry.all():
        rows = _sample_contents(db, page.content_type, per_page)
        total = db.count_contents(content_type=page.content_type)
        approved = db.count_contents(content_type=page.content_type,
                                     status="approved_for_publication")
        parts.append(
            f"<section><h2>{_esc(page.display_name)}<small>{_esc(page.content_type)}"
            f" · {approved}/{total} approvati</small></h2>"
            "<table><tr><th>#</th><th>testo</th><th>categoria</th>"
            "<th>fonte</th><th>qualità</th></tr>")
        for r in rows:
            meta = {}
            try:
                meta = json.loads(r.get("metadata_json") or "{}")
            except ValueError:
                meta = {}
            extra = ""
            if page.content_type == "word_of_the_day":
                extra = (f"<br><small>{_esc(meta.get('part_of_speech'))} — "
                         f"{_esc(meta.get('definition'))}</small>")
            elif page.content_type == "today_in_history":
                extra = (f"<br><small>{_esc(r.get('calendar_key'))} · "
                         f"{_esc(meta.get('year'))} — "
                         f"{_esc(meta.get('description'))}</small>")
            elif page.content_type == "world_curiosity":
                extra = f"<br><small>{_esc(meta.get('location'))}</small>"
            src = ""
            if r.get("source_url"):
                src = (f"<a href='{_esc(r['source_url'])}' rel='noreferrer'>"
                       f"{_esc(r.get('source_name') or 'fonte')}</a>")
            parts.append(
                f"<tr><td>{_esc(r.get('sequence_index'))}</td>"
                f"<td class='text'>{_esc(r.get('text'))}{extra}</td>"
                f"<td>{_esc(r.get('category'))}</td><td>{src}</td>"
                f"<td>{_esc(round(float(r.get('quality_score') or 0), 3))}</td></tr>")
        parts.append("</table></section>")
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path
