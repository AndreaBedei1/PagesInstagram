"""Repository facade over SQLite. One :class:`Database` per process.

Keeps SQL in one place and returns plain dicts to the rest of the engine.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..core.enums import ContentStatus, JobStatus
from ..core.timeutils import utcnow_iso
from .connection import connect, transaction
from .migrations import apply_migrations


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


class Database:
    """Thin repository facade. Construct with :meth:`open`."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- lifecycle ----------------------------------------------------------
    @classmethod
    def open(cls, db_path: str | Path, *, busy_timeout_ms: int = 5000,
             migrate: bool = True) -> "Database":
        conn = connect(db_path, busy_timeout_ms=busy_timeout_ms)
        if migrate:
            apply_migrations(conn)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ================= pages ==============================================
    def upsert_page(self, page_id: str, display_name: str, content_type: str,
                    enabled: bool, configuration_path: str | None) -> None:
        now = utcnow_iso()
        with transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO pages(page_id, display_name, content_type, enabled,
                                  configuration_path, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(page_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    content_type=excluded.content_type,
                    enabled=excluded.enabled,
                    configuration_path=excluded.configuration_path,
                    updated_at=excluded.updated_at
                """,
                (page_id, display_name, content_type, int(enabled),
                 configuration_path, now, now),
            )

    def get_page(self, page_id: str) -> dict | None:
        return _row_to_dict(
            self.conn.execute("SELECT * FROM pages WHERE page_id=?", (page_id,)).fetchone()
        )

    def list_pages(self) -> list[dict]:
        return _rows(self.conn.execute("SELECT * FROM pages ORDER BY page_id"))

    def set_page_enabled(self, page_id: str, enabled: bool) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE pages SET enabled=?, updated_at=? WHERE page_id=?",
                (int(enabled), utcnow_iso(), page_id),
            )

    def is_page_paused(self, page_id: str) -> bool:
        """True only if a pages row exists AND is explicitly disabled (dashboard pause)."""
        row = self.conn.execute(
            "SELECT enabled FROM pages WHERE page_id=?", (page_id,)
        ).fetchone()
        return row is not None and int(row[0]) == 0

    # ================= contents ===========================================
    CONTENT_COLUMNS = (
        "content_type", "language", "text", "original_text", "author",
        "author_display_name", "source_work", "source_year", "source_url",
        "attribution_confidence", "category", "mood", "explanation", "caption",
        "hashtags", "call_to_action", "background_prompt", "normalized_text",
        "content_hash", "semantic_cluster", "quality_score", "status",
    )

    def insert_content(self, data: dict) -> tuple[int | None, bool]:
        """Insert a content row. Returns (id, inserted). Duplicate hash → (existing_id, False).

        Only columns actually supplied are written, so ``NOT NULL DEFAULT``
        columns fall back to their SQL default instead of being forced to NULL.
        A UNIQUE(content_hash) collision is treated as a dedup hit; any other
        integrity error propagates (it signals a real caller mistake).
        """
        payload = dict(data)
        payload.setdefault("language", "it")
        payload.setdefault("status", ContentStatus.DRAFT.value)
        if isinstance(payload.get("hashtags"), (list, tuple)):
            payload["hashtags"] = json.dumps(list(payload["hashtags"]), ensure_ascii=False)
        now = utcnow_iso()
        cols = [c for c in self.CONTENT_COLUMNS if c in payload]
        all_cols = cols + ["created_at", "updated_at"]
        all_vals = [payload[c] for c in cols] + [now, now]
        try:
            with transaction(self.conn):
                cur = self.conn.execute(
                    f"INSERT INTO contents({','.join(all_cols)}) "
                    f"VALUES ({','.join('?' for _ in all_cols)})",
                    all_vals,
                )
                return cur.lastrowid, True
        except sqlite3.IntegrityError as e:
            if "content_hash" in str(e) or "UNIQUE" in str(e).upper():
                existing = self.conn.execute(
                    "SELECT id FROM contents WHERE content_hash=?",
                    (payload.get("content_hash"),),
                ).fetchone()
                if existing:
                    return existing[0], False
            raise

    def get_content(self, content_id: int) -> dict | None:
        return _row_to_dict(
            self.conn.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
        )

    def hash_exists(self, content_hash: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM contents WHERE content_hash=? LIMIT 1", (content_hash,)
        ).fetchone() is not None

    def list_contents(self, *, content_type: str | None = None,
                      status: str | None = None) -> list[dict]:
        q = "SELECT * FROM contents WHERE 1=1"
        args: list[Any] = []
        if content_type:
            q += " AND content_type=?"
            args.append(content_type)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY id"
        return _rows(self.conn.execute(q, args))

    def content_texts(self, content_type: str) -> list[dict]:
        """Lightweight rows for dedup/similarity work."""
        return _rows(self.conn.execute(
            "SELECT id, text, normalized_text, content_hash, mood, category "
            "FROM contents WHERE content_type=?",
            (content_type,),
        ))

    def update_content(self, content_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow_iso()
        cols = ",".join(f"{k}=?" for k in fields)
        with transaction(self.conn):
            self.conn.execute(
                f"UPDATE contents SET {cols} WHERE id=?", (*fields.values(), content_id)
            )

    def count_contents(self, *, content_type: str | None = None,
                       status: str | None = None) -> int:
        q = "SELECT COUNT(*) FROM contents WHERE 1=1"
        args: list[Any] = []
        if content_type:
            q += " AND content_type=?"
            args.append(content_type)
        if status:
            q += " AND status=?"
            args.append(status)
        return int(self.conn.execute(q, args).fetchone()[0])

    def pick_unused_content(self, *, page_id: str, content_type: str,
                            min_quality: float,
                            status: str = ContentStatus.APPROVED_FOR_PUBLICATION) -> dict | None:
        """Approved content not yet PUBLISHED for this page, best quality first."""
        row = self.conn.execute(
            """
            SELECT c.* FROM contents c
            WHERE c.content_type=? AND c.status=?
              AND COALESCE(c.quality_score,0) >= ?
              AND c.id NOT IN (
                  SELECT content_id FROM publication_jobs
                  WHERE page_id=? AND status='PUBLISHED' AND content_id IS NOT NULL
              )
            ORDER BY COALESCE(c.quality_score,0) DESC, RANDOM()
            LIMIT 1
            """,
            (content_type, status, min_quality, page_id),
        ).fetchone()
        return _row_to_dict(row)

    # ================= media_assets =======================================
    def insert_media(self, content_id: int, page_id: str, **paths: Any) -> int:
        now = utcnow_iso()
        meta = paths.get("render_metadata")
        if isinstance(meta, (dict, list)):
            paths["render_metadata"] = json.dumps(meta, ensure_ascii=False)
        cols = ["content_id", "page_id", "background_path", "post_image_path",
                "story_image_path", "post_video_path", "story_video_path",
                "music_path", "render_metadata", "validation_score"]
        values = [content_id, page_id] + [paths.get(c) for c in cols[2:]]
        with transaction(self.conn):
            cur = self.conn.execute(
                f"INSERT INTO media_assets({','.join(cols)},created_at) "
                f"VALUES ({','.join('?' for _ in cols)},?)",
                (*values, now),
            )
            return cur.lastrowid

    def get_media(self, media_id: int) -> dict | None:
        return _row_to_dict(
            self.conn.execute("SELECT * FROM media_assets WHERE id=?", (media_id,)).fetchone()
        )

    def latest_media_for(self, content_id: int, page_id: str) -> dict | None:
        return _row_to_dict(self.conn.execute(
            "SELECT * FROM media_assets WHERE content_id=? AND page_id=? "
            "ORDER BY id DESC LIMIT 1",
            (content_id, page_id),
        ).fetchone())

    # ================= publication_jobs ===================================
    def create_job(self, *, page_id: str, content_id: int | None, media_type: str,
                   idempotency_key: str, scheduled_at: str | None,
                   status: str = JobStatus.DRAFT) -> tuple[int, bool]:
        """Create a job. Idempotent on ``idempotency_key`` → (id, created)."""
        now = utcnow_iso()
        with transaction(self.conn):
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO publication_jobs(
                    page_id, content_id, media_type, idempotency_key, scheduled_at,
                    status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (page_id, content_id, media_type, idempotency_key, scheduled_at,
                 status, now, now),
            )
            if cur.rowcount == 1:
                return cur.lastrowid, True
        existing = self.conn.execute(
            "SELECT id FROM publication_jobs WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        return existing[0], False

    def get_job(self, job_id: int) -> dict | None:
        return _row_to_dict(
            self.conn.execute("SELECT * FROM publication_jobs WHERE id=?", (job_id,)).fetchone()
        )

    def get_job_by_key(self, key: str) -> dict | None:
        return _row_to_dict(self.conn.execute(
            "SELECT * FROM publication_jobs WHERE idempotency_key=?", (key,)
        ).fetchone())

    def update_job(self, job_id: int, **fields: Any) -> None:
        fields["updated_at"] = utcnow_iso()
        cols = ",".join(f"{k}=?" for k in fields)
        with transaction(self.conn):
            self.conn.execute(
                f"UPDATE publication_jobs SET {cols} WHERE id=?",
                (*fields.values(), job_id),
            )

    def list_jobs(self, *, status: str | None = None, page_id: str | None = None,
                  statuses: Iterable[str] | None = None) -> list[dict]:
        q = "SELECT * FROM publication_jobs WHERE 1=1"
        args: list[Any] = []
        if status:
            q += " AND status=?"
            args.append(status)
        if statuses:
            s = list(statuses)
            q += f" AND status IN ({','.join('?' for _ in s)})"
            args.extend(s)
        if page_id:
            q += " AND page_id=?"
            args.append(page_id)
        q += " ORDER BY COALESCE(scheduled_at,''), id"
        return _rows(self.conn.execute(q, args))

    def due_jobs(self, now_iso: str, *, statuses: Iterable[str]) -> list[dict]:
        s = list(statuses)
        q = (
            "SELECT * FROM publication_jobs "
            f"WHERE status IN ({','.join('?' for _ in s)}) "
            "AND scheduled_at IS NOT NULL AND scheduled_at <= ? "
            "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
            "ORDER BY scheduled_at, id"
        )
        return _rows(self.conn.execute(q, (*s, now_iso, now_iso)))

    # ================= daily_content ======================================
    def get_daily_content(self, page_id: str, local_date: str) -> dict | None:
        return _row_to_dict(self.conn.execute(
            "SELECT * FROM daily_content WHERE page_id=? AND local_date=?",
            (page_id, local_date),
        ).fetchone())

    def create_daily_content(self, page_id: str, local_date: str, content_id: int,
                             music_track_id: str | None = None) -> tuple[int, bool]:
        """Assign the day's content for a page. Idempotent on (page_id, local_date)."""
        now = utcnow_iso()
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO daily_content(page_id, local_date, content_id,"
                " music_track_id, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (page_id, local_date, content_id, music_track_id, now, now),
            )
            if cur.rowcount == 1:
                return cur.lastrowid, True
        existing = self.conn.execute(
            "SELECT id FROM daily_content WHERE page_id=? AND local_date=?",
            (page_id, local_date),
        ).fetchone()
        return existing[0], False

    def update_daily_content(self, daily_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow_iso()
        cols = ",".join(f"{k}=?" for k in fields)
        with transaction(self.conn):
            self.conn.execute(
                f"UPDATE daily_content SET {cols} WHERE id=?",
                (*fields.values(), daily_id),
            )

    # ================= publication_logs ===================================
    def log_event(self, *, job_id: int | None, page_id: str | None, event: str,
                  request_summary: str | None = None,
                  response_summary: str | None = None,
                  error: str | None = None) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO publication_logs(job_id, page_id, event, request_summary,"
                " response_summary, error, created_at) VALUES (?,?,?,?,?,?,?)",
                (job_id, page_id, event, request_summary, response_summary, error,
                 utcnow_iso()),
            )

    def job_logs(self, job_id: int) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM publication_logs WHERE job_id=? ORDER BY id", (job_id,)
        ))

    # ================= music ==============================================
    def upsert_track(self, data: dict) -> None:
        data = dict(data)
        data.setdefault("instagram_safe", 1)
        data.setdefault("attribution_required", 0)
        now = utcnow_iso()
        cols = ["track_id", "title", "author", "file_path", "license", "source",
                "category", "mood", "duration_seconds", "bpm", "intensity",
                "instagram_safe", "attribution_required", "attribution_text"]
        values = [data.get(c) for c in cols]
        with transaction(self.conn):
            self.conn.execute(
                f"INSERT INTO music_tracks({','.join(cols)},created_at) "
                f"VALUES ({','.join('?' for _ in cols)},?) "
                "ON CONFLICT(track_id) DO UPDATE SET "
                + ",".join(f"{c}=excluded.{c}" for c in cols[1:]),
                (*values, now),
            )

    def get_track(self, track_id: str) -> dict | None:
        return _row_to_dict(self.conn.execute(
            "SELECT * FROM music_tracks WHERE track_id=?", (track_id,)).fetchone())

    def list_tracks(self, *, category: str | None = None,
                    mood: str | None = None, instagram_safe: bool | None = True) -> list[dict]:
        q = "SELECT * FROM music_tracks WHERE 1=1"
        args: list[Any] = []
        if category:
            q += " AND category=?"
            args.append(category)
        if mood:
            q += " AND mood=?"
            args.append(mood)
        if instagram_safe is not None:
            q += " AND instagram_safe=?"
            args.append(int(instagram_safe))
        return _rows(self.conn.execute(q, args))

    def record_music_usage(self, track_id: str, page_id: str | None,
                           job_id: int | None) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO music_usage(track_id, page_id, job_id, used_at) "
                "VALUES (?,?,?,?)",
                (track_id, page_id, job_id, utcnow_iso()),
            )

    def recent_track_ids(self, page_id: str | None, last_n: int) -> list[str]:
        q = "SELECT track_id FROM music_usage"
        args: list[Any] = []
        if page_id:
            q += " WHERE page_id=?"
            args.append(page_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(last_n)
        return [r[0] for r in self.conn.execute(q, args).fetchall()]

    def track_usage_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT track_id, COUNT(*) c FROM music_usage GROUP BY track_id"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
