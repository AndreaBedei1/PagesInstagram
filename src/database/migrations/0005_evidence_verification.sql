-- Evidence-based verification.
--
-- 0004 introduced source_audit_status, whose strongest value was called
-- `manually_verified`. The name was a promise the pipeline could not keep: any
-- writer could set the string, and the string said nothing about what had
-- actually been read. A label is not evidence.
--
-- These columns store the evidence itself:
--
--   verification_method       how the claim was established
--                             (original_nonfactual | primary_source |
--                              institutional_source | authoritative_reference |
--                              structured_official_dataset |
--                              cross_checked_sources)
--   evidence_summary          the passage from the source that supports it
--   source_title              the title of the page that was read
--   source_checked_at         when it was read
--   source_strength           where that source sits in the hierarchy
--   verified_content_hash     SHA-256 of the normalised claim text
--   verification_tool_version which checker produced this
--
-- NOTE: the table already has a `content_hash` from 0001 — that one is the dedup
-- key, UNIQUE, over a different normalisation. Reusing it would couple the
-- verification binding to deduplication and break both, so the verification
-- hash gets its own column.
--
-- verified_content_hash is the part that cannot be faked cheaply: the gate
-- recomputes it from the stored text, so a claim edited after verification
-- stops being publishable instead of shipping with someone else's evidence
-- attached.
--
-- Forward-only: ADD COLUMN plus indexes, so a populated database upgrades in
-- place. Existing rows get NULL, which the gate reads as "not verified".
--
-- Tokens / app secrets are still NEVER stored in this database.

ALTER TABLE contents ADD COLUMN verification_method       TEXT;
ALTER TABLE contents ADD COLUMN evidence_summary          TEXT;
ALTER TABLE contents ADD COLUMN source_title              TEXT;
ALTER TABLE contents ADD COLUMN source_checked_at         TEXT;
ALTER TABLE contents ADD COLUMN source_strength           TEXT;
ALTER TABLE contents ADD COLUMN verified_content_hash     TEXT;
ALTER TABLE contents ADD COLUMN verification_tool_version TEXT;

CREATE INDEX IF NOT EXISTS idx_contents_verification_method
    ON contents(content_type, verification_method);
CREATE INDEX IF NOT EXISTS idx_contents_evidence_gate
    ON contents(content_type, status, verification_status, source_strength);
