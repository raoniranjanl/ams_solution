"""
Focused, offline eval suite for JobRemediationAgent.get_remediation() -
no real Anthropic, Qdrant, or CloudWatch calls are made. Complements
tests/test_orchestrator_flow.py's job-remediation tests (which exercise
the same agent indirectly through the orchestrator) by calling the agent
directly and covering the grounding-source scenarios plus the two prompt/
retrieval optimizations added alongside this file:
  - match_rule/decision_tree/example_log_events now get threaded into the
    SOP block sent to the LLM (common/sop_store.py + _render_sop_block).
  - complete_json is called with a low, fixed temperature for this agent.

Scenarios covered:
  1. Neither SOP nor logs -> short-circuits, no LLM call.
  2. SOP (with match_rule/decision_tree/example_log_events) + logs that
     satisfy the rule -> the literal rule text reaches the LLM prompt.
  3. SOP matched, no logs -> still recommends the SOP's action (not
     insufficient_information) and requires human approval.
  4. Logs only, no SOP matched -> allowed actions fall back to the fixed
     registry (common.remediation_executor.REQUIRED_PARAMS).
  5. LLM recommends an action outside the allowed set -> anti-
     hallucination backstop overrides to insufficient_information.
  6. complete_json is always called with temperature=0.1.

Run with:  python -m tests.test_job_remediation_agent
"""
from unittest.mock import MagicMock

from agents.job_remediation_agent import JobRemediationAgent
from common.models import Ticket
from common.sop_store import SOP
from config import get_settings


def make_ticket() -> Ticket:
    return Ticket(
        sys_id="glue999", number="INC0030001", table="incident", sys_class_name="incident",
        short_description="agentic-dev-customer-etl-pipeline Glue job failed",
        description="The nightly Glue ETL job failed overnight.",
        cmdb_ci_name="agentic-dev-customer-etl-pipeline",
    )


def make_glue_sop() -> SOP:
    """Mirrors sop_documents/SOP-GLUE-CETL-501.json's shape."""
    return SOP(
        sop_id="SOP-GLUE-CETL-501", title="Glue Job Failure", service="GLUE",
        description="Standard remediation for the Glue job. Only a connection-refused error is safe to "
                     "auto-retry; anything else needs human review.",
        resolution_steps=["retry_glue_job", "manual_review"],
        auto_resolvable=True, risk_level="LOW", blocked_actions=[],
        job_name="agentic-dev-customer-etl-pipeline", log_group="/aws-glue/jobs/logs-v2",
        match_rule="Match on the literal substring 'connection refused' (case-insensitive) appearing "
                   "ANYWHERE within the log event's Failure Reason.",
        decision_tree=[{
            "step": 1,
            "conditions": [
                {"if": "Failure Reason contains 'connection refused'", "action": "retry_glue_job",
                 "approval": "auto", "note": "transient/retryable per policy"},
                {"if": "Failure Reason does NOT contain 'connection refused'", "action": "manual_review",
                 "approval": "human", "note": "root cause must be reviewed"},
            ],
        }],
        example_log_events=[{
            "description": "Real GlueETLJobExceptionEvent - matches the connection-refused rule",
            "raw_event": "RuntimeError: connection refused: required upstream table 'customer_raw' is "
                         "currently locked by another job.",
            "matched_action": "retry_glue_job",
            "why": "Contains the literal substring 'connection refused'.",
        }],
    )


def make_mms_sop() -> SOP:
    """Mirrors sop_documents/SOP-EAM-MMSO-701.json's shape - diagnosed from S3, not CloudWatch."""
    return SOP(
        sop_id="SOP-EAM-MMSO-701", title="Job Failure - Enrollments MMS job", service="S3",
        description="Standard remediation for the Enrollments MMS job.",
        resolution_steps=["move_keyword_files_to_input", "no_action_required", "manual_review"],
        auto_resolvable=True, risk_level="LOW", blocked_actions=[],
        job_name="Enrollments MMS job",
        error_bucket="eam-mmso-error-bucket-2330", keyword_bucket="eam-mmso-keyword-bucket-2330",
        match_rule="Extract the failed step number from the error bucket's latest log file and check "
                   "the keyword bucket's error/ folder for pending files.",
        decision_tree=[{
            "step": 1,
            "conditions": [
                {"if": "step < 5000 AND files exist in error/", "action": "move_keyword_files_to_input",
                 "approval": "auto"},
                {"if": "step < 5000 AND error/ is empty", "action": "no_action_required", "approval": "auto"},
                {"if": "step >= 5000", "action": "manual_review", "approval": "human"},
            ],
        }],
    )


def make_mms_ticket() -> Ticket:
    return Ticket(
        sys_id="mms1", number="INC0040001", table="incident", sys_class_name="incident",
        short_description="Enrollments MMS job failed", description="Enrollments MMS job run failed overnight.",
        cmdb_ci_name="Enrollments MMS job",
    )


def make_agent(llm_client, sop_store, cloudwatch_client, vector_store=None, s3_client=None):
    vector_store = vector_store or MagicMock()
    vector_store.search.return_value = []
    settings = get_settings()
    return JobRemediationAgent(
        vector_store=vector_store, llm_client=llm_client, anthropic_settings=settings.anthropic,
        sop_store=sop_store, cloudwatch_client=cloudwatch_client, s3_client=s3_client,
    )


def test_neither_sop_nor_logs_skips_llm_call():
    llm_client = MagicMock()
    sop_store = MagicMock()
    sop_store.match.return_value = None
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []

    agent = make_agent(llm_client, sop_store, cloudwatch_client)
    result = agent.get_remediation(make_ticket())

    assert result.insufficient_information is True
    assert result.action is None
    assert result.confidence == 0.0
    llm_client.complete_json.assert_not_called()
    print("test_neither_sop_nor_logs_skips_llm_call: PASSED")


def test_sop_and_matching_logs_thread_match_rule_into_prompt():
    captured = {}

    def fake_complete_json(system_prompt, user_prompt, model, temperature=None):
        captured["user_prompt"] = user_prompt
        captured["temperature"] = temperature
        return {
            "job_name": "agentic-dev-customer-etl-pipeline", "action": "retry_glue_job",
            "action_parameters": {"job_name": "agentic-dev-customer-etl-pipeline"},
            "confidence": 0.95, "risk_level": "LOW",
            "rationale": "Failure Reason contains 'connection refused', matching the SOP's match_rule exactly.",
            "requires_human_approval": False, "insufficient_information": False,
        }

    llm_client = MagicMock()
    llm_client.complete_json.side_effect = fake_complete_json
    sop_store = MagicMock()
    sop_store.match.return_value = make_glue_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = [
        "RuntimeError: connection refused: required upstream table 'customer_raw' is currently locked.",
    ]

    agent = make_agent(llm_client, sop_store, cloudwatch_client)
    result = agent.get_remediation(make_ticket())

    # The literal match_rule/decision_tree/example text must actually reach
    # the LLM - this is the core of the prompt-threading optimization.
    assert "connection refused" in captured["user_prompt"]
    assert "Match rule" in captured["user_prompt"]
    assert "Decision tree conditions" in captured["user_prompt"]
    assert "Worked example" in captured["user_prompt"]
    assert captured["temperature"] == 0.1

    assert result.insufficient_information is False
    assert result.action == "retry_glue_job"
    assert result.requires_human_approval is False
    assert result.confidence == 0.95
    print("test_sop_and_matching_logs_thread_match_rule_into_prompt: PASSED")


def test_sop_only_no_logs_still_recommends_action_for_human_approval():
    """
    SOP matched, no logs: the agent must NOT decline with
    insufficient_information just because logs are missing - it should
    still recommend the SOP's actual remediation action (with its
    parameters, extractable from the ticket/SOP job_name, not the logs)
    and require human approval, so the human has something concrete to
    approve/reject rather than a bare "insufficient information".
    """
    captured = {}

    def fake_complete_json(system_prompt, user_prompt, model, temperature=None):
        captured["user_prompt"] = user_prompt
        return {
            "job_name": "agentic-dev-customer-etl-pipeline", "action": "retry_glue_job",
            "action_parameters": {"job_name": "agentic-dev-customer-etl-pipeline"},
            "confidence": 0.6, "risk_level": "LOW",
            "rationale": "SOP-GLUE-CETL-501 matched, but no CloudWatch log evidence was available to "
                         "confirm the specific failure cause. Recommending retry_glue_job (the SOP's "
                         "primary corrective action, which re-runs the job's latest failed run) for "
                         "human review before executing.",
            "requires_human_approval": True, "insufficient_information": False,
        }

    llm_client = MagicMock()
    llm_client.complete_json.side_effect = fake_complete_json
    sop_store = MagicMock()
    sop_store.match.return_value = make_glue_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []  # no logs

    agent = make_agent(llm_client, sop_store, cloudwatch_client)
    result = agent.get_remediation(make_ticket())

    assert "No CloudWatch log events or S3-based evidence were found" in captured["user_prompt"]
    llm_client.complete_json.assert_called_once()  # SOP alone is enough to proceed
    assert result.insufficient_information is False
    assert result.action == "retry_glue_job"
    assert result.requires_human_approval is True
    assert result.confidence == 0.6
    print("test_sop_only_no_logs_still_recommends_action_for_human_approval: PASSED")


def test_sop_only_no_logs_floors_zero_confidence():
    """
    Prompt guidance alone can't GUARANTEE the LLM never returns a literal
    0.0 for this case (model output isn't deterministic) - this is the
    code-level backstop: if a SOP matched, no logs were available, and a
    real action was still recommended, confidence must never surface as
    a literal 0.0 (SOP_ONLY_NO_LOGS_MIN_CONFIDENCE floor).
    """
    from agents.job_remediation_agent import SOP_ONLY_NO_LOGS_MIN_CONFIDENCE

    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "agentic-dev-customer-etl-pipeline", "action": "retry_glue_job",
        "action_parameters": {"job_name": "agentic-dev-customer-etl-pipeline"},
        "confidence": 0.0,  # a stray/degenerate 0.0 despite recommending a real action
        "risk_level": "LOW", "rationale": "SOP matched but no logs were available.",
        "requires_human_approval": True, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = make_glue_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []  # no logs

    agent = make_agent(llm_client, sop_store, cloudwatch_client)
    result = agent.get_remediation(make_ticket())

    assert result.action == "retry_glue_job"
    assert result.confidence == SOP_ONLY_NO_LOGS_MIN_CONFIDENCE
    assert result.confidence > 0.0
    print("test_sop_only_no_logs_floors_zero_confidence: PASSED")


def test_logs_only_restricts_to_fixed_registry():
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "my-app-svc", "action": "restart_ecs_service",
        "action_parameters": {"cluster_name": "prod-cluster", "service_name": "my-app-svc"},
        "confidence": 0.6, "risk_level": None,
        "rationale": "No SOP matched, but logs clearly show a transient connection timeout.",
        "requires_human_approval": True, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = None
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = ["ERROR connection timed out to upstream"]

    agent = make_agent(llm_client, sop_store, cloudwatch_client)
    result = agent.get_remediation(make_ticket())

    llm_client.complete_json.assert_called_once()
    assert result.insufficient_information is False
    assert result.action == "restart_ecs_service"
    print("test_logs_only_restricts_to_fixed_registry: PASSED")


def test_action_outside_allowed_set_overridden_to_insufficient():
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "agentic-dev-customer-etl-pipeline", "action": "restart_lambda",  # not in this SOP's steps
        "action_parameters": {"function_name": "whatever"},
        "confidence": 0.9, "risk_level": "LOW", "rationale": "looks fine",
        "requires_human_approval": False, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = make_glue_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = ["connection refused"]

    agent = make_agent(llm_client, sop_store, cloudwatch_client)
    result = agent.get_remediation(make_ticket())

    assert result.insufficient_information is True
    assert result.action is None
    print("test_action_outside_allowed_set_overridden_to_insufficient: PASSED")


def test_complete_json_called_with_low_temperature():
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "agentic-dev-customer-etl-pipeline", "action": "retry_glue_job",
        "action_parameters": {"job_name": "agentic-dev-customer-etl-pipeline"},
        "confidence": 0.9, "risk_level": "LOW", "rationale": "ok",
        "requires_human_approval": False, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = make_glue_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = ["connection refused"]

    agent = make_agent(llm_client, sop_store, cloudwatch_client)
    agent.get_remediation(make_ticket())

    _, kwargs = llm_client.complete_json.call_args
    assert kwargs.get("temperature") == 0.1
    print("test_complete_json_called_with_low_temperature: PASSED")


# ----------------------------------------------------------------------
# S3-based grounding (SOP-EAM-MMSO-701, "Enrollments MMS job")
# ----------------------------------------------------------------------

def make_s3_client(log_text, error_files=None, input_files=None):
    s3_client = MagicMock()
    s3_client.get_latest_object_text.return_value = log_text

    def fake_list_recent_objects(bucket, prefix=""):
        files = {"error/": error_files or [], "input/": input_files or []}.get(prefix, [])
        return [{"key": f"{prefix}{name}", "last_modified": "2026-01-01T00:00:00", "size": 10} for name in files]

    s3_client.list_recent_objects.side_effect = fake_list_recent_objects
    return s3_client


def test_s3_step_below_5000_with_pending_files_moves_them():
    captured = {}

    def fake_complete_json(system_prompt, user_prompt, model, temperature=None):
        captured["user_prompt"] = user_prompt
        return {
            "job_name": "Enrollments MMS job", "action": "move_keyword_files_to_input",
            "action_parameters": {"keyword_bucket": "eam-mmso-keyword-bucket-2330"},
            "confidence": 0.9, "risk_level": "LOW",
            "rationale": "Step 4000 is below 5000 and error/ has a pending file - moving it back to input/.",
            "requires_human_approval": False, "insufficient_information": False,
        }

    llm_client = MagicMock()
    llm_client.complete_json.side_effect = fake_complete_json
    sop_store = MagicMock()
    sop_store.match.return_value = make_mms_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []  # this job isn't diagnosed via CloudWatch
    s3_client = make_s3_client("Failed at step 4000", error_files=["enrollment_batch_0417.dat"])

    agent = make_agent(llm_client, sop_store, cloudwatch_client, s3_client=s3_client)
    result = agent.get_remediation(make_mms_ticket())

    assert "Failed at step 4000" in captured["user_prompt"]
    assert "error/ folder recent files (1 found)" in captured["user_prompt"]
    assert "input/ folder recent files (0 found)" in captured["user_prompt"]
    assert result.action == "move_keyword_files_to_input"
    assert result.requires_human_approval is False
    assert result.action_parameters == {"keyword_bucket": "eam-mmso-keyword-bucket-2330"}
    print("test_s3_step_below_5000_with_pending_files_moves_them: PASSED")


def test_s3_step_below_5000_no_pending_files_no_action():
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "Enrollments MMS job", "action": "no_action_required", "action_parameters": {},
        "confidence": 0.8, "risk_level": "LOW",
        "rationale": "Step 1000 is below 5000 and error/ is empty - nothing to do.",
        "requires_human_approval": False, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = make_mms_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []
    s3_client = make_s3_client("Failed at step 1000", error_files=[])

    agent = make_agent(llm_client, sop_store, cloudwatch_client, s3_client=s3_client)
    result = agent.get_remediation(make_mms_ticket())

    assert result.action == "no_action_required"
    assert result.insufficient_information is False
    print("test_s3_step_below_5000_no_pending_files_no_action: PASSED")


def test_s3_step_at_or_above_5000_routes_to_human():
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "Enrollments MMS job", "action": "manual_review", "action_parameters": {},
        "confidence": 0.9, "risk_level": "LOW",
        "rationale": "Step 5200 is at/above the 5000 threshold - always needs human review.",
        "requires_human_approval": True, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = make_mms_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []
    s3_client = make_s3_client("Failed at step 5200", error_files=["enrollment_batch_0417.dat"])

    agent = make_agent(llm_client, sop_store, cloudwatch_client, s3_client=s3_client)
    result = agent.get_remediation(make_mms_ticket())

    assert result.action == "manual_review"
    assert result.requires_human_approval is True
    print("test_s3_step_at_or_above_5000_routes_to_human: PASSED")


def test_no_s3_client_configured_is_harmless():
    """Every other (CloudWatch-based) SOP must keep working when s3_client is None (the default)."""
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "agentic-dev-customer-etl-pipeline", "action": "retry_glue_job",
        "action_parameters": {"job_name": "agentic-dev-customer-etl-pipeline"},
        "confidence": 0.9, "risk_level": "LOW", "rationale": "ok",
        "requires_human_approval": False, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = make_glue_sop()
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = ["connection refused"]

    agent = make_agent(llm_client, sop_store, cloudwatch_client)  # s3_client defaults to None
    result = agent.get_remediation(make_ticket())

    assert result.action == "retry_glue_job"
    print("test_no_s3_client_configured_is_harmless: PASSED")


if __name__ == "__main__":
    test_neither_sop_nor_logs_skips_llm_call()
    test_sop_and_matching_logs_thread_match_rule_into_prompt()
    test_sop_only_no_logs_still_recommends_action_for_human_approval()
    test_sop_only_no_logs_floors_zero_confidence()
    test_logs_only_restricts_to_fixed_registry()
    test_action_outside_allowed_set_overridden_to_insufficient()
    test_complete_json_called_with_low_temperature()
    test_s3_step_below_5000_with_pending_files_moves_them()
    test_s3_step_below_5000_no_pending_files_no_action()
    test_s3_step_at_or_above_5000_routes_to_human()
    test_no_s3_client_configured_is_harmless()
    print("All tests passed.")
