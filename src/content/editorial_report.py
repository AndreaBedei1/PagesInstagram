"""HTML and JSON output for ``editorial-stats`` and ``editorial-sample``.

The sample report is the working surface of the review: it puts the claim, the
caption, the call to action, the hashtags, the source link and the current audit
state side by side, so a reviewer can decide without opening five files. The
stats report is the evidence that the rotation is wide enough.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

_CSS = """
body{font:15px/1.55 system-ui,Segoe UI,sans-serif;margin:0;background:#f6f7f9;color:#1c1f23}
main{max-width:1150px;margin:0 auto;padding:30px 20px 70px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 10px}
.sub{color:#5a6371;margin:0 0 22px}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 2px #0001;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #e6e9ee;vertical-align:top}
th{background:#eef1f5;font-weight:600;position:sticky;top:0}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.card{background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 2px #0001;min-width:126px}
.card b{display:block;font-size:23px;line-height:1.2}.card span{color:#5a6371;font-size:13px}
.k{display:inline-block;padding:1px 8px;border-radius:11px;font-size:12px;font-weight:600;white-space:nowrap}
.ok{background:#e3f5e8;color:#1c6b33}.warn{background:#fdf1d8;color:#8a5b00}
.bad{background:#fde5e5;color:#992222}.mut{background:#eceff3;color:#4a5361}
.item{background:#fff;border-radius:10px;padding:16px 18px;margin:10px 0;box-shadow:0 1px 2px #0001}
.item .claim{font-size:16px;font-weight:600;margin:0 0 6px}
.item .meta{color:#5a6371;font-size:13px;margin:2px 0}
.item .cap{margin:8px 0 4px}
.tags{color:#3b6ea5;font-size:13px}
.note{background:#fff8e6;border-left:4px solid #e0a800;padding:12px 16px;margin:18px 0;border-radius:0 8px 8px 0}
a{color:#2b6cb0;word-break:break-all}
@media(prefers-color-scheme:dark){body{background:#15181c;color:#e6e9ee}
table,.card,.item{background:#1e2228;box-shadow:none}th{background:#262b32}
th,td{border-bottom:1px solid #2c323a}.note{background:#2a2418;color:#f0e2c0}a{color:#82b1ff}}
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _badge(status: str) -> str:
    cls = {"manually_verified": "ok", "reachable": "mut", "not_checked": "warn",
           "needs_review": "warn", "unsupported": "bad",
           "broken_source": "bad", "approved": "ok", "rejected": "bad",
           "needs_revision": "warn"}.get(status, "mut")
    return f"<span class='k {cls}'>{escape(status)}</span>"


# ---------------------------------------------------------------------------
def write_stats_json(stats: list, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": _now(),
        "ok": all(s.ok for s in stats),
        "datasets": [s.as_dict() for s in stats],
        "totals": {
            "items": sum(s.items for s in stats),
            "warnings": sum(len(s.warnings) for s in stats),
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def write_stats_html(stats: list, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = [f"<!doctype html><meta charset='utf-8'><title>Statistiche editoriali</title>"
         f"<style>{_CSS}</style><main>",
         "<h1>Ripetitività editoriale</h1>",
         f"<p class='sub'>Generato il {escape(_now())}. Misura ogni quanto un "
         f"lettore rivede la stessa CTA, gli stessi hashtag, lo stesso sfondo.</p>"]

    p.append("<table><tr><th>Dataset</th><th>Elementi</th><th>CTA</th>"
             "<th>CTA vuote</th><th>Combo hashtag</th><th>Hashtag</th>"
             "<th>Prompt</th><th>Categorie</th><th>Mood</th>"
             "<th>Run cat.</th><th>Run mood</th><th>Esito</th></tr>")
    for s in stats:
        p.append(
            f"<tr><td>{escape(s.dataset)}</td><td>{s.items}</td>"
            f"<td>{s.distinct_cta}</td><td>{s.empty_cta}</td>"
            f"<td>{s.distinct_hashtag_sets}</td><td>{s.distinct_hashtags}</td>"
            f"<td>{s.distinct_prompts}</td><td>{s.distinct_categories}</td>"
            f"<td>{s.distinct_moods}</td><td>{s.longest_category_run}</td>"
            f"<td>{s.longest_mood_run}</td>"
            f"<td>{'<span class=\"k ok\">OK</span>' if s.ok else f'<span class=\"k warn\">{len(s.warnings)}</span>'}</td></tr>")
    p.append("</table>")

    for s in stats:
        if not s.warnings:
            continue
        p.append(f"<h2>Segnalazioni — {escape(s.dataset)}</h2><ul>")
        for wmsg in s.warnings[:60]:
            p.append(f"<li>{escape(wmsg)}</li>")
        p.append("</ul>")
        if len(s.warnings) > 60:
            p.append(f"<p class='sub'>… e altre {len(s.warnings) - 60}.</p>")

    for s in stats:
        p.append(f"<h2>CTA più frequenti — {escape(s.dataset)}</h2>"
                 "<table><tr><th>Call to action</th><th>Usi su 1.000</th></tr>")
        for text, n in s.top_cta:
            p.append(f"<tr><td>{escape(text)}</td><td>{n}</td></tr>")
        p.append("</table>")

    p.append("</main>")
    out.write_text("".join(p), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
def write_sample_json(payload: dict, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def write_sample_html(payload: dict, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = [f"<!doctype html><meta charset='utf-8'><title>Campione editoriale</title>"
         f"<style>{_CSS}</style><main>",
         "<h1>Campione editoriale stratificato</h1>",
         f"<p class='sub'>Generato il {escape(payload['generated_at'])} · "
         f"seed <code>{payload['seed']}</code> · "
         f"{payload['total']} contenuti da {len(payload['datasets'])} dataset. "
         f"Stesso seed ⇒ stesso campione.</p>",
         "<div class='note'>Questo campione è ciò che una persona deve leggere. "
         "Un contenuto non compreso qui resta <code>not_checked</code> e non è "
         "pubblicabile in produzione.</div>"]

    for ds in payload["datasets"]:
        p.append(f"<h2>{escape(ds['dataset'])} — {len(ds['items'])} contenuti "
                 f"su {ds['total_items']}</h2>")
        p.append("<p class='sub'>Strati coperti: "
                 + escape(", ".join(ds.get("strata_covered", [])[:24])) + "</p>")
        for it in ds["items"]:
            tags = " ".join(it.get("hashtags") or [])
            src = it.get("source_url") or ""
            p.append(
                "<div class='item'>"
                f"<p class='claim'>#{it['sequence_index']} · {escape(it.get('text') or '')}</p>"
                f"<p class='meta'>categoria <b>{escape(str(it.get('category')))}</b> · "
                f"mood <b>{escape(str(it.get('mood')))}</b>"
                + (f" · calendario <b>{escape(str(it.get('calendar_key')))}</b>"
                   if it.get("calendar_key") else "")
                + (f" · anno <b>{escape(str((it.get('metadata') or {}).get('year')))}</b>"
                   if (it.get("metadata") or {}).get("year") else "")
                + f" · generatore <code>{escape(str(it.get('id')))}</code></p>"
                + (f"<p class='cap'>{escape(it.get('caption') or '')}</p>")
                + (f"<p class='meta'>CTA: <i>{escape(it.get('call_to_action') or '— nessuna —')}</i></p>")
                + f"<p class='tags'>{escape(tags)}</p>"
                + (f"<p class='meta'>fonte: {escape(it.get('source_name') or '')} — "
                   f"<a href='{escape(src)}' rel='noreferrer'>{escape(src)}</a></p>" if src else "")
                + "<p class='meta'>audit fonte " + _badge(it.get("source_audit_status") or "not_checked")
                + " · editoriale " + _badge(it.get("editorial_status") or "not_checked") + "</p>"
                + (("<p class='meta'><span class='k warn'>"
                    + escape("; ".join(it["warnings"])[:200]) + "</span></p>")
                   if it.get("warnings") else "")
                + "</div>")
    p.append("</main>")
    out.write_text("".join(p), encoding="utf-8")
    return out
