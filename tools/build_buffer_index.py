"""Extract representative frames from the buffer and build a local index.

ffprobe answers whether a file is well formed; it says nothing about whether the
text fits inside the safe area or whether the background came out legible. Three
frames per Reel — opening, middle, closing — and one HTML page make that
answerable by looking.

Frames and index stay out of Git: they are regenerable from the buffer, and the
buffer is regenerable from the corpus.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.video.ffmpeg import resolve_ffmpeg  # noqa: E402
from src.video.media_audit import safe_area_box  # noqa: E402

OUT = ROOT / "reports" / "buffer_index"
FRAMES = OUT / "frames"


def grab(ffmpeg: str, video: Path, when: float, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(when),
         "-i", str(video), "-frames:v", "1", "-vf", "scale=270:-1", str(target)],
        capture_output=True)
    return target.exists() and target.stat().st_size > 0


def main() -> int:
    ffmpeg = resolve_ffmpeg(None)
    db = sqlite3.connect(ROOT / "database" / "content.sqlite")
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT j.page_id, j.scheduled_at, j.output_path, c.text "
        "FROM publication_jobs j LEFT JOIN contents c ON c.id = j.content_id "
        "WHERE j.output_path IS NOT NULL "
        "ORDER BY j.page_id, j.scheduled_at").fetchall()

    FRAMES.mkdir(parents=True, exist_ok=True)
    cards, missing = [], 0
    for n, row in enumerate(rows):
        video = Path(row["output_path"])
        if not video.exists():
            missing += 1
            continue
        stem = f"{n:03d}_{video.stem[:40]}"
        shots = []
        for label, when in (("inizio", 0.2), ("centro", 4.0), ("fine", 7.6)):
            frame = FRAMES / f"{stem}_{label}.jpg"
            if grab(ffmpeg, video, when, frame):
                shots.append((label, frame.relative_to(OUT).as_posix()))
        cards.append({"page_id": row["page_id"], "date": row["scheduled_at"][:10],
                      "file": video.name, "text": row["text"] or "",
                      "shots": shots})

    box = safe_area_box()
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Buffer media</title><style>",
        "body{font:15px/1.5 system-ui,Segoe UI,sans-serif;margin:0;background:#14171a;color:#e6e9ee}",
        "main{max-width:1200px;margin:0 auto;padding:28px 18px 60px}",
        "h1{font-size:24px;margin:0 0 6px}.sub{color:#9aa4b2;margin:0 0 22px}",
        ".card{background:#1e2228;border-radius:10px;padding:14px;margin:14px 0}",
        ".shots{display:flex;gap:10px;margin-top:10px}.shots img{width:270px;border-radius:6px}",
        ".meta{color:#9aa4b2;font-size:13px}.claim{font-weight:600;margin:2px 0 6px}",
        "</style><main>",
        f"<h1>Buffer media — {len(cards)} Reel</h1>",
        f"<p class='sub'>Tre fotogrammi per file (inizio, centro, fine). "
        f"Area sicura 9:16: x {box['left']}–{box['right']}, "
        f"y {box['top']}–{box['bottom']}. "
        f"{missing} file mancanti.</p>",
    ]
    for card in cards:
        parts.append(
            f"<div class='card'><p class='claim'>{escape(card['text'][:150])}</p>"
            f"<p class='meta'>{escape(card['page_id'])} · {escape(card['date'])} "
            f"· {escape(card['file'])}</p><div class='shots'>"
            + "".join(f"<img src='{escape(src)}' alt='{escape(label)}' loading='lazy'>"
                      for label, src in card["shots"])
            + "</div></div>")
    parts.append("</main>")
    (OUT / "index.html").write_text("".join(parts), encoding="utf-8")

    summary = {"reels": len(cards), "frames": len(list(FRAMES.glob("*.jpg"))),
               "missing_files": missing, "safe_area": box}
    (OUT / "index.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"indice: {OUT / 'index.html'}")
    return 0 if missing == 0 and cards else 1


if __name__ == "__main__":
    raise SystemExit(main())
