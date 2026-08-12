-- Evergreen multi-page content model.
--
-- Adds the columns required by the deterministic selection policies:
--   * cyclic_ordered    -> contents.sequence_index (0..N-1, unique per type)
--   * calendar_rotating -> contents.calendar_key   ('MM-DD')
-- plus provenance columns for fact-checked content types. No existing migration
-- is modified; this file only ADDs columns/indexes, so an already-populated
-- database upgrades in place.
--
-- Tokens / app secrets are still NEVER stored in this database.

ALTER TABLE contents ADD COLUMN sequence_index      INTEGER;
ALTER TABLE contents ADD COLUMN calendar_key        TEXT;     -- 'MM-DD'
ALTER TABLE contents ADD COLUMN metadata_json       TEXT;     -- free-form JSON
ALTER TABLE contents ADD COLUMN verification_status TEXT;     -- verified | unverified | original
ALTER TABLE contents ADD COLUMN verified_at         TEXT;     -- 'YYYY-MM-DD'
ALTER TABLE contents ADD COLUMN source_name         TEXT;     -- source_url already exists

CREATE INDEX IF NOT EXISTS idx_contents_type_sequence
    ON contents(content_type, sequence_index);
CREATE INDEX IF NOT EXISTS idx_contents_type_calendar
    ON contents(content_type, calendar_key);
-- idx_contents_type_status already exists (0001); recreated defensively for DBs
-- restored from a partial dump.
CREATE INDEX IF NOT EXISTS idx_contents_type_status
    ON contents(content_type, status);
CREATE INDEX IF NOT EXISTS idx_contents_verification
    ON contents(content_type, verification_status);

-- The day's rotation state, so a replay of the same date reproduces the same
-- background seed even if the anchor date is later changed by mistake.
ALTER TABLE daily_content ADD COLUMN cycle_number    INTEGER;
ALTER TABLE daily_content ADD COLUMN sequence_index  INTEGER;

-- Rolling media retention: remember that a published job's local file was
-- deleted, so cleanup is idempotent and auditable.
ALTER TABLE publication_jobs ADD COLUMN media_deleted_at TEXT;
