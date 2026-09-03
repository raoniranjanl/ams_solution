-- Tracks every member (by pMEME_HEALTH_ID) that jobs/process_eam_member_kwd_files.py
-- has already exported to a Legacy_<timestamp>.txt output object, so a member
-- flagged once is never re-exported on a later run. Run via psql or Adminer.

CREATE TABLE IF NOT EXISTS member_export_log (
    id SERIAL PRIMARY KEY,
    pmeme_health_id VARCHAR(64) NOT NULL UNIQUE,
    exported_value VARCHAR(64) NOT NULL,
    source_key VARCHAR(255),
    sop_id VARCHAR(64),
    exported_at TIMESTAMP NOT NULL DEFAULT now()
);
