INSERT INTO eam (member_id, mbi, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, existing_member, new_member, effective_date, term_date)
VALUES
    ('M1001', '1A2B3C4D5E6', TRUE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, FALSE, CURRENT_DATE, '9999-12-31'),
    ('M1002', '9Z8Y7X6W5V4', FALSE, 'MAPD', FALSE, TRUE, FALSE, FALSE, TRUE, FALSE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (member_id) DO UPDATE SET
    mbi = EXCLUDED.mbi,
    has_active = EXCLUDED.has_active,
    planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went,
    member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered,
    id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went,
    existing_member = EXCLUDED.existing_member,
    new_member = EXCLUDED.new_member,
    effective_date = EXCLUDED.effective_date,
    term_date = EXCLUDED.term_date,
    updated_at = now();

INSERT INTO facets (mem_health_id, hic, has_active, planname, confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered, id_card_triggered, pcp_letter_went, existing_member, new_member, effective_date, term_date)
VALUES
    ('M1001', '1A2B3C4D5E6', TRUE, 'MA', TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, FALSE, CURRENT_DATE, '9999-12-31'),
    ('M1002', '9Z8Y7X6W5V4', FALSE, 'MAPD', FALSE, TRUE, FALSE, FALSE, TRUE, FALSE, TRUE, CURRENT_DATE, '9999-12-31')
ON CONFLICT (mem_health_id) DO UPDATE SET
    hic = EXCLUDED.hic,
    has_active = EXCLUDED.has_active,
    planname = EXCLUDED.planname,
    confirmation_letter_went = EXCLUDED.confirmation_letter_went,
    member_exist_in_eam = EXCLUDED.member_exist_in_eam,
    welcome_kit_triggered = EXCLUDED.welcome_kit_triggered,
    id_card_triggered = EXCLUDED.id_card_triggered,
    pcp_letter_went = EXCLUDED.pcp_letter_went,
    existing_member = EXCLUDED.existing_member,
    new_member = EXCLUDED.new_member,
    effective_date = EXCLUDED.effective_date,
    term_date = EXCLUDED.term_date,
    updated_at = now();
