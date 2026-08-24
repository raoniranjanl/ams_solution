"""
Deterministic member-eligibility rule for SOP-EAM-MEMVALID-801.

The eam and facets tables share the same status-flag/plan/date columns
(see sql/create_eam_facets_tables.sql), so this single check function is
applied to both an eam row and a facets row - a member is only eligible
when BOTH records independently pass.
"""
from datetime import date
from typing import Dict, List, Optional, Tuple

VALID_PLAN_NAMES = {"MA", "MAPD"}

REQUIRED_TRUE_FLAGS = [
    "has_active",
    "confirmation_letter_went",
    "member_exist_in_eam",
    "welcome_kit_triggered",
    "id_card_triggered",
    "pcp_letter_went",
]


def check_record_eligibility(record: Dict, today: Optional[date] = None) -> Tuple[bool, List[str]]:
    """
    Returns (is_eligible, failed_check_names). `today` is injectable for
    testing; defaults to the current date.
    """
    today = today or date.today()
    failed: List[str] = []

    for flag in REQUIRED_TRUE_FLAGS:
        if not record.get(flag):
            failed.append(flag)

    if record.get("planname") not in VALID_PLAN_NAMES:
        failed.append("planname")

    effective_date = record.get("effective_date")
    if not effective_date or effective_date > today:
        failed.append("effective_date")

    term_date = record.get("term_date")
    if not term_date or term_date < today:
        failed.append("term_date")

    return (len(failed) == 0, failed)
