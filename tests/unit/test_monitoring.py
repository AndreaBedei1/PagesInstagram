"""Tests for status summary, JSON report and HTML preview generation."""
from __future__ import annotations

import json

from src.core.enums import ContentStatus, JobStatus
from src.core.paths import Paths
from src.monitoring import build_preview_html, export_report, status_summary


def _seed(db):
    cid, _ = db.insert_content(dict(
        content_type="motivational", text="Un passo alla volta.",
        normalized_text="x", content_hash="mon-1", mood="calm",
        caption="Spiegazione.", quality_score=0.9,
        status=ContentStatus.APPROVED_FOR_PUBLICATION))
    jid, _ = db.create_job(page_id="motivational_it", content_id=cid,
                           media_type="feed_video", idempotency_key="mon-k1",
                           scheduled_at="2026-07-21T10:30:00Z", status=JobStatus.SCHEDULED)
    db.insert_media(cid, "motivational_it", post_image_path="/x/a.png",
                    post_video_path="/x/a.mp4", validation_score=0.88,
                    render_metadata={"music_track_id": "tone_calm_1"})
    return cid, jid


def test_status_summary(tmp_db):
    _seed(tmp_db)
    s = status_summary(tmp_db)
    assert s["contents_by_status"]["approved_for_publication"] == 1
    assert s["jobs_by_status"]["SCHEDULED"] == 1
    assert len(s["upcoming_jobs"]) == 1


def test_export_report(tmp_db, tmp_path):
    _seed(tmp_db)
    out = export_report(tmp_db, tmp_path / "r.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "recent_jobs" in data
    assert data["jobs_by_status"]["SCHEDULED"] == 1


def test_build_preview_html(tmp_db, tmp_path):
    _seed(tmp_db)
    paths = Paths.create()
    out = build_preview_html(tmp_db, paths, out_path=tmp_path / "preview.html")
    html = out.read_text(encoding="utf-8")
    assert "Instagram Content Engine" in html
    assert "Un passo alla volta" in html
