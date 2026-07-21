-- Initial schema for the Instagram Content Engine.
-- Timestamps are ISO-8601 UTC strings. Tokens are NEVER stored here.

-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id            TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    enabled            INTEGER NOT NULL DEFAULT 1,
    configuration_path TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contents (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type           TEXT NOT NULL,
    language               TEXT NOT NULL DEFAULT 'it',
    text                   TEXT NOT NULL,
    original_text          TEXT,
    author                 TEXT,
    author_display_name    TEXT,
    source_work            TEXT,
    source_year            TEXT,
    source_url             TEXT,
    attribution_confidence TEXT,          -- high | medium | low
    category               TEXT,
    mood                   TEXT,
    explanation            TEXT,
    caption                TEXT,
    hashtags               TEXT,          -- JSON array
    call_to_action         TEXT,
    background_prompt      TEXT,
    normalized_text        TEXT,          -- lowercased, punctuation-stripped
    content_hash           TEXT NOT NULL UNIQUE,
    semantic_cluster       INTEGER,       -- id of a near-duplicate cluster (nullable)
    quality_score          REAL,
    status                 TEXT NOT NULL DEFAULT 'draft',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contents_type_status
    ON contents(content_type, status);
CREATE INDEX IF NOT EXISTS idx_contents_mood ON contents(mood);
CREATE INDEX IF NOT EXISTS idx_contents_cluster ON contents(semantic_cluster);

-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_assets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id        INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    page_id           TEXT NOT NULL,
    background_path   TEXT,
    post_image_path   TEXT,
    story_image_path  TEXT,
    post_video_path   TEXT,
    story_video_path  TEXT,
    music_path        TEXT,
    render_metadata   TEXT,               -- JSON (seed, font, sizes, checks...)
    validation_score  REAL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_content ON media_assets(content_id);
CREATE INDEX IF NOT EXISTS idx_media_page ON media_assets(page_id);

-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS publication_jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id          TEXT NOT NULL,
    content_id       INTEGER REFERENCES contents(id) ON DELETE SET NULL,
    media_type       TEXT NOT NULL,       -- feed_image | feed_video | story_* | reel
    idempotency_key  TEXT NOT NULL UNIQUE,-- page:media_type:scheduled_date
    scheduled_at     TEXT,                -- ISO-8601 UTC
    generated_at     TEXT,
    published_at     TEXT,
    status           TEXT NOT NULL DEFAULT 'DRAFT',
    retry_count      INTEGER NOT NULL DEFAULT 0,
    next_retry_at    TEXT,
    last_error       TEXT,
    remote_media_id  TEXT,
    container_id     TEXT,
    output_path      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON publication_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_page_sched ON publication_jobs(page_id, scheduled_at);

-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS publication_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           INTEGER REFERENCES publication_jobs(id) ON DELETE CASCADE,
    page_id          TEXT,
    event            TEXT NOT NULL,       -- container_create | status | publish | error | retry
    request_summary  TEXT,                -- redacted (no tokens)
    response_summary TEXT,                -- redacted (no tokens)
    error            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_job ON publication_logs(job_id);

-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS music_tracks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             TEXT NOT NULL UNIQUE,
    title                TEXT NOT NULL,
    author               TEXT,
    file_path            TEXT NOT NULL,
    license              TEXT,
    source               TEXT,
    category             TEXT,            -- one of MUSIC_CATEGORIES
    mood                 TEXT,
    duration_seconds     REAL,
    bpm                  REAL,
    intensity            REAL,            -- 0..1
    instagram_safe       INTEGER NOT NULL DEFAULT 1,
    attribution_required INTEGER NOT NULL DEFAULT 0,
    attribution_text     TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_music_cat_mood ON music_tracks(category, mood);

-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS music_usage (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id  TEXT NOT NULL,
    page_id   TEXT,
    job_id    INTEGER REFERENCES publication_jobs(id) ON DELETE SET NULL,
    used_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_music_usage_track ON music_usage(track_id);
CREATE INDEX IF NOT EXISTS idx_music_usage_page ON music_usage(page_id, used_at);
