"""Tests for the database layer: migrations, dedup, job idempotency, music usage."""
from __future__ import annotations

from src.core.enums import ContentStatus, JobStatus
from src.database import Database, current_version


def test_migrations_applied(tmp_path):
    db = Database.open(tmp_path / "m.sqlite")
    assert current_version(db.conn) is not None
    tables = {
        r[0]
        for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for expected in ("pages", "contents", "publication_jobs", "media_assets",
                     "publication_logs", "music_tracks", "music_usage"):
        assert expected in tables
    db.close()


def test_migrations_idempotent(tmp_path):
    p = tmp_path / "m2.sqlite"
    Database.open(p).close()
    db = Database.open(p)  # second open reruns apply_migrations
    # no pending migrations means nothing crashes and version persists
    assert current_version(db.conn) is not None
    db.close()


def test_content_dedup_by_hash(tmp_db: Database):
    a, ins_a = tmp_db.insert_content(dict(
        content_type="motivational", text="Ciao", normalized_text="ciao",
        content_hash="h1"))
    b, ins_b = tmp_db.insert_content(dict(
        content_type="motivational", text="Ciao!", normalized_text="ciao",
        content_hash="h1"))
    assert ins_a is True
    assert ins_b is False
    assert a == b
    assert tmp_db.count_contents(content_type="motivational") == 1


def test_job_idempotency(approved_content):
    db, cid = approved_content
    key = "motivational_it:feed_image:2026-07-21"
    j1, c1 = db.create_job(page_id="motivational_it", content_id=cid,
                           media_type="feed_image", idempotency_key=key,
                           scheduled_at="2026-07-21T10:30:00Z")
    j2, c2 = db.create_job(page_id="motivational_it", content_id=cid,
                           media_type="feed_image", idempotency_key=key,
                           scheduled_at="2026-07-21T10:30:00Z")
    assert c1 is True and c2 is False
    assert j1 == j2


def test_pick_unused_skips_published(approved_content):
    db, cid = approved_content
    # Same page already published this content -> should not be picked again
    key = "motivational_it:feed_image:2026-07-20"
    jid, _ = db.create_job(page_id="motivational_it", content_id=cid,
                           media_type="feed_image", idempotency_key=key,
                           scheduled_at="2026-07-20T10:30:00Z")
    db.update_job(jid, status=JobStatus.PUBLISHED)
    assert db.pick_unused_content(page_id="motivational_it",
                                  content_type="motivational", min_quality=0.5) is None
    # A different page may still use it
    assert db.pick_unused_content(page_id="famous_quotes_it",
                                  content_type="motivational", min_quality=0.5) is not None


def test_music_usage_recency(tmp_db: Database):
    for i in range(3):
        tmp_db.upsert_track(dict(track_id=f"t{i}", title=f"Track {i}",
                                 file_path=f"/x/t{i}.mp3", category="calm",
                                 mood="calm", instagram_safe=1))
    tmp_db.record_music_usage("t0", "motivational_it", None)
    tmp_db.record_music_usage("t1", "motivational_it", None)
    recent = tmp_db.recent_track_ids("motivational_it", 5)
    assert recent[:2] == ["t1", "t0"]
    counts = tmp_db.track_usage_counts()
    assert counts.get("t0") == 1 and counts.get("t1") == 1
