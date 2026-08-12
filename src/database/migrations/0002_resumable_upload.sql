-- Resumable (direct) upload support + persistent daily content assignment.

-- Resumable upload state on each job. No tokens / no Authorization headers here;
-- upload_uri is the rupload.facebook.com endpoint (contains no credentials).
ALTER TABLE publication_jobs ADD COLUMN upload_method       TEXT;
ALTER TABLE publication_jobs ADD COLUMN upload_status       TEXT;
ALTER TABLE publication_jobs ADD COLUMN upload_offset       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE publication_jobs ADD COLUMN upload_size_bytes   INTEGER;
ALTER TABLE publication_jobs ADD COLUMN upload_uri          TEXT;
ALTER TABLE publication_jobs ADD COLUMN upload_started_at   TEXT;
ALTER TABLE publication_jobs ADD COLUMN upload_completed_at TEXT;

-- One content (and one music track) per page per local day, shared by ALL
-- formats of that day (Reel + Story). Guarantees feed and story never diverge.
CREATE TABLE IF NOT EXISTS daily_content (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         TEXT NOT NULL,
    local_date      TEXT NOT NULL,             -- YYYY-MM-DD in the page timezone
    content_id      INTEGER REFERENCES contents(id) ON DELETE SET NULL,
    music_track_id  TEXT,
    media_asset_id  INTEGER REFERENCES media_assets(id) ON DELETE SET NULL,
    video_path      TEXT,                      -- the shared 9:16 video (Reel+Story)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(page_id, local_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_page_date ON daily_content(page_id, local_date);
