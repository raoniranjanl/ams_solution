-- Adds existing_member / new_member / effective_date / term_date columns
-- to eam and facets tables that were already created before these columns existed.
-- Safe to re-run.

ALTER TABLE eam
    ADD COLUMN IF NOT EXISTS existing_member BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS new_member BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS effective_date DATE,
    ADD COLUMN IF NOT EXISTS term_date DATE;

ALTER TABLE facets
    ADD COLUMN IF NOT EXISTS existing_member BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS new_member BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS effective_date DATE,
    ADD COLUMN IF NOT EXISTS term_date DATE;
