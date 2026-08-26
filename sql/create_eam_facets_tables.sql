-- Creates the eam and facets tables in the ams_agentic Postgres database.
-- Run via psql or Adminer's SQL command box (http://localhost:8080).

CREATE TABLE IF NOT EXISTS eam (
    id SERIAL PRIMARY KEY,
    member_id VARCHAR(64) NOT NULL UNIQUE,
    mbi VARCHAR(64) NOT NULL,
    has_active BOOLEAN NOT NULL DEFAULT FALSE,
    planname VARCHAR(32),
    confirmation_letter_went BOOLEAN NOT NULL DEFAULT FALSE,
    member_exist_in_eam BOOLEAN NOT NULL DEFAULT FALSE,
    welcome_kit_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    id_card_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    pcp_letter_went BOOLEAN NOT NULL DEFAULT FALSE,
    existing_member BOOLEAN NOT NULL DEFAULT FALSE,
    new_member BOOLEAN NOT NULL DEFAULT FALSE,
    effective_date DATE,
    term_date DATE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS facets (
    id SERIAL PRIMARY KEY,
    mem_health_id VARCHAR(64) NOT NULL UNIQUE,
    hic VARCHAR(64) NOT NULL,
    has_active BOOLEAN NOT NULL DEFAULT FALSE,
    planname VARCHAR(32),
    confirmation_letter_went BOOLEAN NOT NULL DEFAULT FALSE,
    member_exist_in_eam BOOLEAN NOT NULL DEFAULT FALSE,
    welcome_kit_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    id_card_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    pcp_letter_went BOOLEAN NOT NULL DEFAULT FALSE,
    existing_member BOOLEAN NOT NULL DEFAULT FALSE,
    new_member BOOLEAN NOT NULL DEFAULT FALSE,
    effective_date DATE,
    term_date DATE,
    latest_changes_on_member_pcp BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
