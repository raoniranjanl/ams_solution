"""
Guardrails validator for the Job Remediation AI Agent.

Modeled directly on the reference architecture's guardrails_validator
Lambda: every one of these checks must pass before a remediation action
is allowed to auto-execute. If ANY check fails, the ticket is routed to
a human via the same approval-link email flow used for low-confidence
incident routing (see agents/ams_orchestrator_agent.py).

Checks (in order, mirroring the reference design):
  1. A matching SOP was found at all.
  2. The recommended action is literally one of the SOP's resolution_steps.
  3. The SOP marks this job class as auto_resolvable.
  4. The SOP's risk_level is in the configured allowed set (default LOW/MEDIUM).
  5. The action is not in the SOP's own blocked_actions, nor in the
     globally configured REMEDIATION_BLOCKED_ACTIONS.
  6. Confidence meets the configured threshold.
  7. The LLM itself didn't set requires_human_approval=True.
  8. The recommendation isn't itself flagged insufficient_information.
  9. Every AWS parameter the action actually needs (cluster_name,
     db_identifier, instance_id, etc.) was actually extracted - this is
     a second safety net on top of the agent's own anti-hallucination
     instructions, since executing restart_ecs_service with a missing or
     empty cluster_name would otherwise throw an unhandled AWS API error.
"""
import logging
from typing import Optional

from common.models import GuardrailResult, RemediationRecommendation
from common.remediation_executor import REQUIRED_PARAMS
from common.sop_store import SOP
from config import RemediationSettings

logger = logging.getLogger(__name__)


class GuardrailsValidator:
    def __init__(self, settings: RemediationSettings):
        self.settings = settings

    def validate(self, recommendation: RemediationRecommendation, sop: Optional[SOP]) -> GuardrailResult:
        failed = []

        # Check 1: SOP exists
        if sop is None:
            failed.append("no_matching_sop")
            # Nothing further to check meaningfully without a SOP.
            return GuardrailResult(passed=False, failed_checks=failed)

        # Check 2: action is one of the SOP's allowed resolution steps
        if not recommendation.action or recommendation.action not in sop.resolution_steps:
            failed.append("action_not_in_sop_resolution_steps")

        # Check 3: SOP allows auto-resolution for this job class at all
        if not sop.auto_resolvable:
            failed.append("sop_not_auto_resolvable")

        # Check 4: risk level is within the allowed set
        if sop.risk_level not in self.settings.allowed_risk_levels:
            failed.append(f"risk_level_{sop.risk_level}_not_allowed")

        # Check 5: action isn't blocked (per-SOP or global)
        if recommendation.action and recommendation.action in sop.blocked_actions:
            failed.append("action_blocked_by_sop")
        if recommendation.action and recommendation.action in self.settings.blocked_actions:
            failed.append("action_blocked_globally")

        # Check 6: confidence threshold
        if recommendation.confidence < self.settings.confidence_threshold:
            failed.append("confidence_below_threshold")

        # Check 7: the LLM's own opinion on whether a human should look at this
        if recommendation.requires_human_approval:
            failed.append("llm_flagged_requires_human_approval")

        # Check 8: the agent explicitly said it lacks grounding
        if recommendation.insufficient_information:
            failed.append("insufficient_information")

        # Check 9: every required AWS parameter for this action was actually extracted
        if recommendation.action:
            required = REQUIRED_PARAMS.get(recommendation.action, [])
            missing = [p for p in required if not (recommendation.action_parameters or {}).get(p)]
            for p in missing:
                failed.append(f"missing_required_parameter:{p}")

        passed = len(failed) == 0
        if not passed:
            logger.info("Guardrails FAILED for %s: %s", recommendation.ticket_number, failed)
        else:
            logger.info("Guardrails PASSED for %s -> auto-executing '%s'",
                        recommendation.ticket_number, recommendation.action)
        return GuardrailResult(passed=passed, failed_checks=failed)
