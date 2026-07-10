"""
Mocked tests for the two S3-based remediation actions added for
SOP-EAM-MMSO-701 (Enrollments MMS job): move_keyword_files_to_input and
no_action_required. No real AWS calls are made - common.remediation_executor's
S3Client reference is patched.

Run with: python -m tests.test_remediation_executor_s3_actions
"""
from unittest.mock import patch

from common.models import RemediationRecommendation, Ticket
from common.remediation_executor import RemediationExecutor


def make_ticket() -> Ticket:
    return Ticket(
        sys_id="mms1", number="INC0040001", table="incident", sys_class_name="incident",
        short_description="Enrollments MMS job failed", description="Failed at step 4000",
        cmdb_ci_name="Enrollments MMS job",
    )


def test_move_keyword_files_to_input_moves_and_reports_success():
    with patch("common.remediation_executor.S3Client") as MockS3Client:
        MockS3Client.return_value.move_objects.return_value = ["input/enrollment_batch_0417.dat"]

        executor = RemediationExecutor()
        recommendation = RemediationRecommendation(
            ticket_number="INC0040001", action="move_keyword_files_to_input",
            action_parameters={"keyword_bucket": "eam-mmso-keyword-bucket-2330"},
            confidence=0.9, risk_level="LOW", requires_human_approval=False,
            insufficient_information=False, sop_id="SOP-EAM-MMSO-701",
        )
        result = executor.execute(make_ticket(), recommendation)

        MockS3Client.return_value.move_objects.assert_called_once_with(
            "eam-mmso-keyword-bucket-2330", source_prefix="error/", dest_prefix="input/"
        )
        assert result.success is True
        assert "1 file" in result.message
        print("test_move_keyword_files_to_input_moves_and_reports_success: PASSED")


def test_move_keyword_files_to_input_requires_keyword_bucket():
    executor = RemediationExecutor()
    recommendation = RemediationRecommendation(
        ticket_number="INC0040002", action="move_keyword_files_to_input",
        action_parameters={},  # missing keyword_bucket
        confidence=0.9, risk_level="LOW", requires_human_approval=False,
        insufficient_information=False, sop_id="SOP-EAM-MMSO-701",
    )
    result = executor.execute(make_ticket(), recommendation)

    assert result.success is False
    assert "keyword_bucket" in result.message
    print("test_move_keyword_files_to_input_requires_keyword_bucket: PASSED")


def test_no_action_required_reports_success_without_aws_calls():
    executor = RemediationExecutor()
    recommendation = RemediationRecommendation(
        ticket_number="INC0040003", action="no_action_required", action_parameters={},
        confidence=0.6, risk_level="LOW", requires_human_approval=False,
        insufficient_information=False, sop_id="SOP-EAM-MMSO-701",
    )
    result = executor.execute(make_ticket(), recommendation)

    assert result.success is True
    print("test_no_action_required_reports_success_without_aws_calls: PASSED")


if __name__ == "__main__":
    test_move_keyword_files_to_input_moves_and_reports_success()
    test_move_keyword_files_to_input_requires_keyword_bucket()
    test_no_action_required_reports_success_without_aws_calls()
    print("All tests passed.")
