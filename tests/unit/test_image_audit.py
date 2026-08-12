"""What can be wrong with a still, and the copy that goes with it.

The Reel audit asks ffprobe about codecs, frame rates and moov atoms. A still
has none of those, and asking ffprobe about a PNG produced either nonsense or —
on a machine with no ffmpeg, which is this one — a failure that said nothing
about the file and stopped the canary on media that was perfectly fine.

The last test here is about words rather than pixels: an image post cannot be
watched or listened to, so a caption that says so would be wrong on the account,
where nobody would see a test failure.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from src.core.meta_api import IMAGE_MAX_FILE_BYTES, IMAGE_POST_SIZE
from src.publishing.image_audit import audit_image

ROOT = Path(__file__).resolve().parents[2]


def _still(path: Path, size=IMAGE_POST_SIZE, fmt="PNG") -> Path:
    Image.new("RGB", size, (30, 40, 60)).save(path, fmt)
    return path


def test_a_correct_still_passes(tmp_path):
    check = audit_image(_still(tmp_path / "ok.png"), page_id="p")
    assert check.ok, check.problems
    assert (check.width, check.height) == IMAGE_POST_SIZE
    assert check.image_format == "PNG"
    assert check.size_bytes > 0


def test_a_jpeg_of_the_right_shape_passes_too(tmp_path):
    """Meta documents both; the pipeline writes PNG, but the audit is not the pipeline."""
    check = audit_image(_still(tmp_path / "ok.jpg", fmt="JPEG"))
    assert check.ok, check.problems


def test_the_wrong_shape_is_the_whole_point(tmp_path):
    """A 9:16 frame in an image post is cropped by Instagram, not rejected by it."""
    check = audit_image(_still(tmp_path / "tall.png", size=(1080, 1920)))
    assert not check.ok
    assert any("1080x1350" in p for p in check.problems), check.problems


def test_a_video_where_a_still_belongs(tmp_path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 2048)
    check = audit_image(mp4)
    assert not check.ok
    assert any("JPEG o PNG" in p for p in check.problems)


def test_a_file_that_is_not_there_and_one_that_is_empty(tmp_path):
    absent = audit_image(tmp_path / "assente.png")
    assert not absent.ok and "inesistente" in absent.problems[0]

    empty = tmp_path / "vuoto.png"
    empty.write_bytes(b"")
    assert not audit_image(empty).ok


def test_a_png_extension_on_something_that_is_not_a_png(tmp_path):
    fake = tmp_path / "bugiardo.png"
    fake.write_bytes(b"non sono un'immagine" * 50)
    check = audit_image(fake)
    assert not check.ok
    assert any("non leggibile" in p for p in check.problems)


def test_the_size_limit_is_the_documented_one(tmp_path):
    big = tmp_path / "grande.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (IMAGE_MAX_FILE_BYTES + 1))
    check = audit_image(big)
    assert not check.ok
    assert any("MB" in p for p in check.problems)


def test_the_limits_have_exactly_one_home():
    """Same rule as the Reel constants: no second copy of a published number."""
    copies = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if path.name == "meta_api.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"8\s*\*\s*1_000_000|1080,\s*1350\)", text):
            copies.append(str(path.relative_to(ROOT)))
    assert not copies, f"i limiti dell'immagine sono duplicati in: {copies}"


# ---- the report -----------------------------------------------------------
def test_the_report_carries_the_image_targets(tmp_path):
    from src.video.media_audit import write_report

    out = write_report([], tmp_path / "r.json",
                       images=[audit_image(_still(tmp_path / "a.png"))])
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["image_targets"]["size"] == "1080x1350"
    assert payload["image_targets"]["max_file_bytes"] == IMAGE_MAX_FILE_BYTES
    assert len(payload["images"]) == 1 and payload["images"][0]["ok"] is True


def test_one_bad_still_fails_the_whole_report(tmp_path):
    from src.video.media_audit import write_report

    out = write_report([], tmp_path / "r2.json",
                       images=[audit_image(_still(tmp_path / "b.png")),
                               audit_image(_still(tmp_path / "c.png",
                                                  size=(1080, 1080)))])
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is False


# ---- the words ------------------------------------------------------------
#: Directives that only make sense in front of a video. Deliberately narrow:
#: "ascoltare" and "volume" appear all over this corpus as ordinary Italian
#: ("chi ascolta non ha la nostra storia in testa"), and a guard that flagged
#: those would be turned off within a week.
FORMAT_DIRECTIVES = re.compile(
    r"guarda il video|guarda questo (?:video|reel)|attiva l'?audio|"
    r"alza il volume|con l'?audio acceso|#reels?\b|in questo reel|nel reel|"
    r"scorri il video|swipe", re.I)


@pytest.mark.parametrize("column", ["caption", "call_to_action", "hashtags"])
def test_no_published_copy_tells_the_reader_to_watch_or_listen(column):
    db_path = ROOT / "database" / "content.sqlite"
    if not db_path.exists():
        pytest.skip("nessun database locale")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT id, {column} AS v FROM contents WHERE {column} IS NOT NULL").fetchall()
    conn.close()
    offenders = [(r["id"], str(r["v"])) for r in rows
                 if FORMAT_DIRECTIVES.search(str(r["v"]))]
    assert not offenders, (
        f"{len(offenders)} testi rimandano a un formato che non pubblichiamo "
        f"più: {offenders[:3]}")
