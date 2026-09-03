-- Adds latest_changes_on_member_pcp to the facets table, backfilling all
-- existing rows to TRUE. Safe to re-run.

ALTER TABLE facets
    ADD COLUMN IF NOT EXISTS latest_changes_on_member_pcp BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE facets SET latest_changes_on_member_pcp = TRUE;
