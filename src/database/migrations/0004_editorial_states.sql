-- Editorial review states.
--
-- 0003 gave contents a `verification_status`, which the dataset sets about
-- itself. That single flag was being used as proof of factual quality, which it
-- never was: it only said "a source string is present and well formed".
--
-- Two independent axes are added next to it:
--
--   source_audit_status   what the audit found out about the cited URL
--                         (not_checked | reachable | manually_verified |
--                          needs_review | unsupported | broken_source)
--   editorial_status      whether a human approved the text itself
--                         (not_checked | approved | needs_revision | rejected)
--
-- Production publication requires, for fact-checked types:
--   verification_status = verified
--   source_audit_status = manually_verified
--   editorial_status    = approved
--
-- Forward-only: this file only ADDs columns and indexes, so a populated
-- database upgrades in place. Existing rows default to the honest values —
-- not_checked — rather than being retroactively declared verified.
--
-- Tokens / app secrets are still NEVER stored in this database.

ALTER TABLE contents ADD COLUMN source_audit_status TEXT DEFAULT 'not_checked';
ALTER TABLE contents ADD COLUMN source_audited_at   TEXT;  -- ISO 8601 UTC
ALTER TABLE contents ADD COLUMN source_audit_note   TEXT;
ALTER TABLE contents ADD COLUMN editorial_status    TEXT DEFAULT 'not_checked';
ALTER TABLE contents ADD COLUMN editorial_note      TEXT;
ALTER TABLE contents ADD COLUMN source_tier         TEXT;

-- Rows created before this migration have NULL, which must read as 'not_checked'
-- rather than as an absence of opinion.
UPDATE contents SET source_audit_status = 'not_checked'
    WHERE source_audit_status IS NULL;
UPDATE contents SET editorial_status = 'not_checked'
    WHERE editorial_status IS NULL;

-- The selection queries filter on (type, status, editorial, source audit), so
-- the index carries all four.
CREATE INDEX IF NOT EXISTS idx_contents_production_ready
    ON contents(content_type, status, editorial_status, source_audit_status);
CREATE INDEX IF NOT EXISTS idx_contents_source_audit
    ON contents(source_audit_status);
