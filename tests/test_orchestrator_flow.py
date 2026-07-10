"""
Lightweight, offline test of the AMS Orchestrator pipeline using mocks -
no real ServiceNow, Qdrant, Anthropic, or SMTP calls are made. Covers:
  - routing: high confidence / low confidence / insufficient information
  - remediation: guardrails pass (auto-execute) / guardrails fail (approval email)
  - ApprovalStore and GuardrailsValidator directly

Run with:  python -m tests.test_orchestrator_flow
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

from agents.ams_orchestrator_agent import AMSOrchestratorAgent
from common.approval_store import ApprovalStore
from common.audit_store import AuditStore
from common.guardrails import GuardrailsValidator
from common.models import RemediationRecommendation, RoutingResult, Ticket
from common.sop_store import SOP
from config import get_settings


def make_ticket(number="INC0010001") -> Ticket:
    return Ticket(
        sys_id="abc123",
        number=number,
        table="incident",
        sys_class_name="incident",
        short_description="Nightly ETL job failed with error code 137",
        description="The nightly batch ETL job 'LOAD_SALES_DATA' failed overnight.",
        cmdb_ci_name="ETL-SERVER-01",
    )


def make_approval_store() -> ApprovalStore:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ApprovalStore(path)


def make_audit_store() -> AuditStore:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return AuditStore(path)


def make_orchestrator(settings, servicenow_client, incident_router, job_remediation, llm_client,
                       approval_store, vector_store, sop_store, guardrails, remediation_executor, audit_store):
    return AMSOrchestratorAgent(
        settings=settings,
        servicenow_client=servicenow_client,
        incident_router_agent=incident_router,
        job_remediation_agent=job_remediation,
        llm_client=llm_client,
        approval_store=approval_store,
        vector_store=vector_store,
        sop_store=sop_store,
        guardrails=guardrails,
        remediation_executor=remediation_executor,
        audit_store=audit_store,
    )


def make_etl_sop() -> SOP:
    return SOP(
        sop_id="SOP-ETL-101", title="ETL Job Failure", applies_to=["etl"],
        description="ETL remediation", resolution_steps=["restart_job", "rerun_failed_job"],
        auto_resolvable=True, risk_level="LOW", blocked_actions=[],
    )


def test_high_confidence_routing_then_remediation_auto_executes():
    settings = get_settings()
    settings.confidence_threshold = 0.5
    settings.remediation.confidence_threshold = 0.5
    settings.remediation.allowed_risk_levels = ("LOW", "MEDIUM")
    settings.remediation.blocked_actions = ()

    servicenow_client = MagicMock()
    llm_client = MagicMock()
    vector_store = MagicMock()
    remediation_executor = MagicMock()

    from common.models import ExecutionResult
    remediation_executor.execute.return_value = ExecutionResult(success=True, message="Restarted job.")

    incident_router = MagicMock()
    incident_router.route.return_value = RoutingResult(
        ticket_number="INC0010001", assignment_group="Batch-Ops-L2", confidence=0.91,
        rationale="Matches prior ETL failures.", insufficient_information=False,
    )

    job_remediation = MagicMock()
    job_remediation.get_remediation.return_value = RemediationRecommendation(
        ticket_number="INC0010001", job_name="LOAD_SALES_DATA", action="restart_job",
        confidence=0.9, risk_level="LOW", rationale="Matches SOP-ETL-101 and 3 similar past failures.",
        requires_human_approval=False, insufficient_information=False, sop_id="SOP-ETL-101",
    )

    def fake_complete_json(system_prompt, user_prompt, model):
        if "ticket_type" in system_prompt:
            return {"ticket_type": "incident", "rationale": "incident table"}
        return {"is_job_failure": True, "confidence": 0.88, "rationale": "ETL job failure keywords present",
                 "job_name": "LOAD_SALES_DATA"}
    llm_client.complete_json.side_effect = fake_complete_json

    sop_store = MagicMock()
    sop_store.get.return_value = make_etl_sop()

    guardrails = GuardrailsValidator(settings.remediation)
    audit_store = make_audit_store()

    orchestrator = make_orchestrator(settings, servicenow_client, incident_router, job_remediation,
                                      llm_client, make_approval_store(), vector_store, sop_store,
                                      guardrails, remediation_executor, audit_store)

    orchestrator.process_ticket(make_ticket())

    servicenow_client.update_assignment_group.assert_called_once_with("abc123", "Batch-Ops-L2", table="incident")
    remediation_executor.execute.assert_called_once()
    vector_store.upsert_job_remediation_feedback.assert_called_once()
    audit_records = audit_store.for_ticket("INC0010001")
    assert len(audit_records) == 1
    assert audit_records[0].actor == "ai_auto"
    assert audit_records[0].result == "success"
    print("test_high_confidence_routing_then_remediation_auto_executes: PASSED")


def test_remediation_guardrails_fail_sends_approval_email():
    settings = get_settings()
    settings.confidence_threshold = 0.5
    settings.remediation.confidence_threshold = 0.9  # deliberately high so the recommendation fails this check

    servicenow_client = MagicMock()
    llm_client = MagicMock()
    vector_store = MagicMock()
    remediation_executor = MagicMock()

    incident_router = MagicMock()
    incident_router.route.return_value = RoutingResult(
        ticket_number="INC0010002", assignment_group="Batch-Ops-L2", confidence=0.91,
        rationale="ok", insufficient_information=False,
    )

    job_remediation = MagicMock()
    job_remediation.get_remediation.return_value = RemediationRecommendation(
        ticket_number="INC0010002", job_name="LOAD_SALES_DATA", action="restart_job",
        confidence=0.6,  # below the 0.9 threshold set above
        risk_level="LOW", rationale="Some match but not fully confident.",
        requires_human_approval=False, insufficient_information=False, sop_id="SOP-ETL-101",
    )

    def fake_complete_json(system_prompt, user_prompt, model):
        if "ticket_type" in system_prompt:
            return {"ticket_type": "incident", "rationale": "incident table"}
        return {"is_job_failure": True, "confidence": 0.8, "rationale": "job failure", "job_name": "LOAD_SALES_DATA"}
    llm_client.complete_json.side_effect = fake_complete_json

    sop_store = MagicMock()
    sop_store.get.return_value = make_etl_sop()

    guardrails = GuardrailsValidator(settings.remediation)
    approval_store = make_approval_store()
    audit_store = make_audit_store()

    orchestrator = make_orchestrator(settings, servicenow_client, incident_router, job_remediation,
                                      llm_client, approval_store, vector_store, sop_store,
                                      guardrails, remediation_executor, audit_store)

    with patch("agents.ams_orchestrator_agent.send_remediation_approval_email") as mock_send:
        mock_send.return_value = True
        orchestrator.process_ticket(make_ticket("INC0010002"))
        assert mock_send.call_count == 1
        _, kwargs = mock_send.call_args
        assert "confidence_below_threshold" in kwargs["failed_checks"]

    remediation_executor.execute.assert_not_called()
    audit_records = audit_store.for_ticket("INC0010002")
    assert len(audit_records) == 1
    assert audit_records[0].result == "pending_approval"
    print("test_remediation_guardrails_fail_sends_approval_email: PASSED")


def test_guardrails_validator_directly():
    settings = get_settings()
    settings.remediation.confidence_threshold = 0.65
    settings.remediation.allowed_risk_levels = ("LOW", "MEDIUM")
    settings.remediation.blocked_actions = ("force_start_job",)
    guardrails = GuardrailsValidator(settings.remediation)
    sop = make_etl_sop()

    good = RemediationRecommendation(
        ticket_number="X", action="restart_job", confidence=0.9, risk_level="LOW",
        requires_human_approval=False, insufficient_information=False, sop_id=sop.sop_id,
    )
    assert guardrails.validate(good, sop).passed is True

    no_sop = guardrails.validate(good, None)
    assert no_sop.passed is False
    assert "no_matching_sop" in no_sop.failed_checks

    blocked = RemediationRecommendation(
        ticket_number="X", action="force_start_job", confidence=0.9, risk_level="LOW",
        requires_human_approval=False, insufficient_information=False, sop_id=sop.sop_id,
    )
    result = guardrails.validate(blocked, sop)
    assert result.passed is False
    assert "action_blocked_globally" in result.failed_checks
    print("test_guardrails_validator_directly: PASSED")


def test_guardrails_catches_missing_aws_parameters():
    """
    A real AWS action (restart_ec2_instance requires instance_id per
    common.remediation_executor.REQUIRED_PARAMS) with no action_parameters
    extracted must fail guardrails even if everything else looks fine -
    this is the safety net behind the agent's own anti-hallucination
    instructions.
    """
    settings = get_settings()
    settings.remediation.confidence_threshold = 0.5
    settings.remediation.allowed_risk_levels = ("LOW", "MEDIUM")
    settings.remediation.blocked_actions = ()
    guardrails = GuardrailsValidator(settings.remediation)

    ec2_sop = SOP(
        sop_id="SOP-EC2-401", title="EC2 Instance Failure", service="EC2",
        description="EC2 recovery", resolution_steps=["restart_ec2_instance", "stop_start_ec2"],
        auto_resolvable=True, risk_level="MEDIUM", blocked_actions=[],
    )

    missing_params = RemediationRecommendation(
        ticket_number="X", action="restart_ec2_instance", action_parameters={},  # no instance_id!
        confidence=0.9, risk_level="MEDIUM", requires_human_approval=False,
        insufficient_information=False, sop_id=ec2_sop.sop_id,
    )
    result = guardrails.validate(missing_params, ec2_sop)
    assert result.passed is False
    assert "missing_required_parameter:instance_id" in result.failed_checks

    with_params = RemediationRecommendation(
        ticket_number="X", action="restart_ec2_instance", action_parameters={"instance_id": "i-0abc123"},
        confidence=0.9, risk_level="MEDIUM", requires_human_approval=False,
        insufficient_information=False, sop_id=ec2_sop.sop_id,
    )
    assert guardrails.validate(with_params, ec2_sop).passed is True
    print("test_guardrails_catches_missing_aws_parameters: PASSED")


def test_approval_store_remediation_kind_roundtrip():
    store = make_approval_store()
    token = store.create_approval(
        kind="remediation", ticket_number="INC0010003", sys_id="xyz789", table="incident",
        confidence=0.5, rationale="test", action="restart_job", job_name="LOAD_SALES_DATA",
        sop_id="SOP-ETL-101", risk_level="LOW",
        short_description="short desc", description="full desc", cmdb_ci_name="SRV-01", ttl_hours=1,
    )
    record = store.get(token)
    assert record is not None
    assert record.kind == "remediation"
    assert record.action == "restart_job"
    assert record.status == "pending"
    print("test_approval_store_remediation_kind_roundtrip: PASSED")


# ----------------------------------------------------------------------
# Job Remediation AI Agent: SOP-and/or-logs decision rule
# ----------------------------------------------------------------------

def make_remediation_ticket() -> Ticket:
    return Ticket(
        sys_id="abc999", number="INC0020001", table="incident", sys_class_name="incident",
        short_description="my-app-svc ECS service unhealthy", description="Service repeatedly failing health checks.",
        cmdb_ci_name="my-app-svc",
    )


def test_job_remediation_neither_sop_nor_logs_skips_llm_call():
    from agents.job_remediation_agent import JobRemediationAgent

    vector_store = MagicMock()
    llm_client = MagicMock()
    sop_store = MagicMock()
    sop_store.match.return_value = None  # no SOP matched
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []  # no logs found

    settings = get_settings()
    agent = JobRemediationAgent(
        vector_store=vector_store, llm_client=llm_client, anthropic_settings=settings.anthropic,
        sop_store=sop_store, cloudwatch_client=cloudwatch_client,
    )

    result = agent.get_remediation(make_remediation_ticket())

    assert result.insufficient_information is True
    assert result.action is None
    assert result.confidence == 0.0
    llm_client.complete_json.assert_not_called()  # short-circuited, no LLM call
    vector_store.search.assert_not_called()
    print("test_job_remediation_neither_sop_nor_logs_skips_llm_call: PASSED")


def test_job_remediation_sop_only_proceeds():
    from agents.job_remediation_agent import JobRemediationAgent

    vector_store = MagicMock()
    vector_store.search.return_value = []
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "my-app-svc", "action": "restart_ecs_service",
        "action_parameters": {"cluster_name": "prod-cluster", "service_name": "my-app-svc"},
        "confidence": 0.8, "risk_level": "MEDIUM",
        "rationale": "SOP matched; no logs were available to confirm root cause.",
        "requires_human_approval": False, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = SOP(
        sop_id="SOP-EKS-101", title="ECS container crash", service="ECS",
        resolution_steps=["restart_ecs_service", "restart_ecs_node", "scale_out_ecs", "manual_review"],
        auto_resolvable=True, risk_level="MEDIUM",
    )
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []  # no logs

    settings = get_settings()
    agent = JobRemediationAgent(
        vector_store=vector_store, llm_client=llm_client, anthropic_settings=settings.anthropic,
        sop_store=sop_store, cloudwatch_client=cloudwatch_client,
    )

    result = agent.get_remediation(make_remediation_ticket())

    llm_client.complete_json.assert_called_once()  # SOP alone is enough to proceed
    assert result.insufficient_information is False
    assert result.action == "restart_ecs_service"
    assert result.action_parameters == {"cluster_name": "prod-cluster", "service_name": "my-app-svc"}
    print("test_job_remediation_sop_only_proceeds: PASSED")


def test_job_remediation_logs_only_restricts_to_registry():
    from agents.job_remediation_agent import JobRemediationAgent

    vector_store = MagicMock()
    vector_store.search.return_value = []
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "my-app-svc", "action": "restart_ecs_service",
        "action_parameters": {"cluster_name": "prod-cluster", "service_name": "my-app-svc"},
        "confidence": 0.6, "risk_level": None,
        "rationale": "No SOP matched, but logs clearly show a transient connection timeout.",
        "requires_human_approval": True, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = None  # no SOP
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = [
        "2026-07-08T10:00:00Z ERROR connection timed out to upstream",
        "2026-07-08T10:00:05Z ERROR connection timed out to upstream",
    ]

    settings = get_settings()
    agent = JobRemediationAgent(
        vector_store=vector_store, llm_client=llm_client, anthropic_settings=settings.anthropic,
        sop_store=sop_store, cloudwatch_client=cloudwatch_client,
    )

    result = agent.get_remediation(make_remediation_ticket())

    llm_client.complete_json.assert_called_once()  # logs alone is enough to proceed
    assert result.insufficient_information is False
    assert result.action == "restart_ecs_service"  # in the fixed registry, so allowed
    print("test_job_remediation_logs_only_restricts_to_registry: PASSED")


def test_job_remediation_rejects_action_outside_allowed_set():
    """If a SOP matched, the model must stay within ITS resolution_steps -
    even a real, valid action name from elsewhere gets overridden to
    insufficient_information if it's not in this SOP's own list."""
    from agents.job_remediation_agent import JobRemediationAgent

    vector_store = MagicMock()
    vector_store.search.return_value = []
    llm_client = MagicMock()
    llm_client.complete_json.return_value = {
        "job_name": "my-app-svc", "action": "retry_glue_job",  # not in this SOP's resolution_steps
        "action_parameters": {"job_name": "my-app-svc"},
        "confidence": 0.9, "risk_level": "MEDIUM", "rationale": "looks fine",
        "requires_human_approval": False, "insufficient_information": False,
    }
    sop_store = MagicMock()
    sop_store.match.return_value = SOP(
        sop_id="SOP-EKS-101", title="ECS container crash", service="ECS",
        resolution_steps=["restart_ecs_service"], auto_resolvable=True, risk_level="MEDIUM",
    )
    cloudwatch_client = MagicMock()
    cloudwatch_client.fetch_logs_for.return_value = []

    settings = get_settings()
    agent = JobRemediationAgent(
        vector_store=vector_store, llm_client=llm_client, anthropic_settings=settings.anthropic,
        sop_store=sop_store, cloudwatch_client=cloudwatch_client,
    )

    result = agent.get_remediation(make_remediation_ticket())
    assert result.insufficient_information is True
    assert result.action is None
    print("test_job_remediation_rejects_action_outside_allowed_set: PASSED")


if __name__ == "__main__":
    test_high_confidence_routing_then_remediation_auto_executes()
    test_remediation_guardrails_fail_sends_approval_email()
    test_guardrails_validator_directly()
    test_guardrails_catches_missing_aws_parameters()
    test_approval_store_remediation_kind_roundtrip()
    test_job_remediation_neither_sop_nor_logs_skips_llm_call()
    test_job_remediation_sop_only_proceeds()
    test_job_remediation_logs_only_restricts_to_registry()
    test_job_remediation_rejects_action_outside_allowed_set()
    print("All tests passed.")
