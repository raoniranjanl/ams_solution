-- Three test scenarios for SOP-EAM-MEMVALID-801:
--   M2001: fully matches, latest_changes_on_member_pcp = TRUE
--          -> "All matching", NOT exported, NOT MMS-forwarded
--   M2002: fully matches, latest_changes_on_member_pcp = FALSE
--          -> "All matching", NOT exported, IS MMS-forwarded
--   M2003: mismatch between eam and facets (eam passes, facets fails has_active)
--          -> "not matching", exported to Legacy (HIC from facets)

-- M2001 - fully matching, pcp already updated (TRUE)
INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date)
VALUES ('M2001', 'AAAA11111111', TRUE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date, latest_changes_on_member_pcp)
VALUES ('M2001', 'AAAA11111111', TRUE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31', TRUE)
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    latest_changes_on_member_pcp = EXCLUDED.latest_changes_on_member_pcp, updated_at = now();

-- M2002 - fully matching, pcp NOT yet updated (FALSE) -> MMS forward candidate
INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date)
VALUES ('M2002', 'BBBB22222222', TRUE, 'MAPD', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date, latest_changes_on_member_pcp)
VALUES ('M2002', 'BBBB22222222', TRUE, 'MAPD', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31', FALSE)
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    latest_changes_on_member_pcp = EXCLUDED.latest_changes_on_member_pcp, updated_at = now();

-- M2006 - fully matching, pcp NOT yet updated (FALSE) -> MMS forward candidate (same pattern as M2002)
INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date)
VALUES ('M2006', 'FFFF66666666', TRUE, 'MAPD', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date, latest_changes_on_member_pcp)
VALUES ('M2006', 'FFFF66666666', TRUE, 'MAPD', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31', FALSE)
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    latest_changes_on_member_pcp = EXCLUDED.latest_changes_on_member_pcp, updated_at = now();

-- M2005 - fully matching, pcp NOT yet updated (FALSE) -> MMS forward candidate (same pattern as M2002)
INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date)
VALUES ('M2005', 'EEEE55555555', TRUE, 'MAPD', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date, latest_changes_on_member_pcp)
VALUES ('M2005', 'EEEE55555555', TRUE, 'MAPD', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31', FALSE)
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    latest_changes_on_member_pcp = EXCLUDED.latest_changes_on_member_pcp, updated_at = now();

-- M2007 - CROSS-TABLE VALUE DISAGREEMENT: both eam and facets individually
-- pass eligibility, but planname differs (MA vs MAPD) - new check_cross_table_equality
-- catches this even though both eam_failed_checks/facets_failed_checks are empty.
-- Also proves should_forward_to_mms() correctly excludes it despite pcp=false.
INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date)
VALUES ('M2007', 'GGGG77777777', TRUE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date, latest_changes_on_member_pcp)
VALUES ('M2007', 'GGGG77777777', TRUE, 'MAPD', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31', FALSE)
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    latest_changes_on_member_pcp = EXCLUDED.latest_changes_on_member_pcp, updated_at = now();

-- M2003 - MISMATCH: eam fully passes, facets fails on has_active (data disagrees between tables)
INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date)
VALUES ('M2003', 'CCCC33333333', TRUE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date, latest_changes_on_member_pcp)
VALUES ('M2003', 'CCCC33333333', FALSE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31', FALSE)
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    latest_changes_on_member_pcp = EXCLUDED.latest_changes_on_member_pcp, updated_at = now();

-- M2004 - MISMATCH (same pattern as M2003): eam fully passes, facets fails on has_active
INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date)
VALUES ('M2004', 'DDDD44444444', TRUE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, effective_date, term_date, latest_changes_on_member_pcp)
VALUES ('M2004', 'DDDD44444444', FALSE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, CURRENT_DATE, '9999-12-31', FALSE)
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic, has_active = EXCLUDED.has_active, planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went, member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered, id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went, effective_date = EXCLUDED.effective_date, term_date = EXCLUDED.term_date,
    latest_changes_on_member_pcp = EXCLUDED.latest_changes_on_member_pcp, updated_at = now();
