"""
Remediation executor - REAL AWS actions via boto3.

Ported from the reference architecture's remediation_executor Lambda,
adapted to run as a plain Python function call from this project's
orchestrator / approval_server instead of as a Step-Functions-invoked
Lambda. Behavior and action names are kept identical so nothing was lost
in the port - see the action dispatch table below.

Every action pulls its required parameters (cluster/service names,
instance IDs, ARNs, etc.) from `recommendation.action_parameters`, which
the Job Remediation AI Agent is responsible for extracting from the
ticket text - see agents/job_remediation_agent.py. If a required
parameter is missing, the action raises ValueError, which
common/guardrails.py also checks for up front (missing_required_parameter)
so most of these never even get called without what they need.

See the README's "AWS Prerequisites" section for the IAM permissions and
pre-existing AWS resources (ECS clusters/services, RDS instances, EC2
instances, Lambda functions, Step Functions state machines, Glue jobs)
each action needs.
"""
import logging
import time
from typing import Callable, Dict

from common.aws_client import get_client
from common.models import ExecutionResult, Ticket
from common.s3_client import S3Client
from common.sop_store import SOPStore
from config import get_settings

logger = logging.getLogger(__name__)

_REGISTRY: Dict[str, Callable] = {}

# Declared for common/guardrails.py's "missing_required_parameter" check.
REQUIRED_PARAMS: Dict[str, list] = {
    "restart_ecs_service": ["cluster_name", "service_name"],
    "restart_ecs_task": ["cluster_name", "task_arn"],
    "restart_ecs_node": ["cluster_name"],
    "restart_rds_instance": ["db_identifier"],
    "reboot_rds": ["db_identifier"],
    "restart_ec2_instance": ["instance_id"],
    "reboot_ec2": ["instance_id"],
    "stop_start_ec2": ["instance_id"],
    "scale_ecs_service": ["cluster_name", "service_name"],
    "scale_out_ecs": ["cluster_name", "service_name"],
    "restart_lambda": ["function_name"],
    "clear_lambda_error": ["function_name"],
    "retry_step_function": ["state_machine_arn"],
    "retry_glue_job": ["job_name"],
    "manual_review": [],
    "move_keyword_files_to_input": ["keyword_bucket"],
    "no_action_required": [],
    "export_eligible_member_hic": [],
    "flag_member_ineligible": [],
}


def register(action_name: str):
    def decorator(fn):
        _REGISTRY[action_name] = fn
        return fn
    return decorator


class RemediationExecutor:
    def execute(self, ticket: Ticket, recommendation) -> ExecutionResult:
        action = (recommendation.action or "").lower().replace(" ", "_").replace("-", "_")
        handler = _REGISTRY.get(action)
        if not handler:
            for key, fn in _REGISTRY.items():
                if key in action or action in key:
                    handler = fn
                    break
        if not handler:
            logger.error("No executor registered for action '%s' (ticket %s)", action, ticket.number)
            return ExecutionResult(success=False, message=f"No executor implemented for action '{action}'.")
        try:
            params = recommendation.action_parameters or {}
            return handler(params, ticket, recommendation)
        except Exception as e:
            logger.exception("Execution of '%s' failed for %s", action, ticket.number)
            return ExecutionResult(success=False, message=f"Execution raised an exception: {e}")


# ----------------------------------------------------------------------
# Action implementations - real boto3 calls against AWS.
# Requires AWS credentials to be configured (see common/aws_client.py and
# the README's AWS Prerequisites section) and the target resource
# (ECS service, RDS instance, EC2 instance, etc.) to already exist.
# ----------------------------------------------------------------------

@register("restart_ecs_service")
def restart_ecs_service(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    cluster = params.get("cluster_name", "")
    service = params.get("service_name", "")
    if not cluster or not service:
        raise ValueError(f"restart_ecs_service requires cluster_name and service_name, got: {params}")

    ecs = get_client("ecs")
    logger.info("Restarting ECS service: cluster=%s service=%s", cluster, service)
    resp = ecs.update_service(cluster=cluster, service=service, forceNewDeployment=True)
    deployment_id = resp["service"]["deployments"][0]["id"]
    return ExecutionResult(success=True, message=f"Force-deployed ECS service {service} on cluster {cluster} (deployment {deployment_id}).")


@register("restart_ecs_task")
def restart_ecs_task(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    task_arn = params.get("task_arn", "")
    cluster = params.get("cluster_name", "")
    if not task_arn or not cluster:
        raise ValueError("restart_ecs_task requires task_arn and cluster_name")

    ecs = get_client("ecs")
    logger.info("Stopping ECS task: %s", task_arn)
    ecs.stop_task(cluster=cluster, task=task_arn, reason="AMS Agentic AI auto-remediation")
    return ExecutionResult(success=True, message=f"Stopped ECS task {task_arn} (ECS will restart it if part of a service).")


@register("restart_ecs_node")
def restart_ecs_node(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    cluster = params.get("cluster_name", "")
    if not cluster:
        raise ValueError("restart_ecs_node requires cluster_name")
    container_instance_arn = params.get("container_instance_arn", "")

    ecs = get_client("ecs")
    ec2 = get_client("ec2")
    instances = ecs.list_container_instances(cluster=cluster)["containerInstanceArns"]
    if not instances:
        raise ValueError(f"No container instances in cluster {cluster}")

    target = container_instance_arn if container_instance_arn in instances else instances[0]
    detail = ecs.describe_container_instances(cluster=cluster, containerInstances=[target])
    ec2_id = detail["containerInstances"][0]["ec2InstanceId"]

    ec2.reboot_instances(InstanceIds=[ec2_id])
    return ExecutionResult(success=True, message=f"Rebooted EC2 instance {ec2_id} (ECS node in cluster {cluster}).")


@register("restart_rds_instance")
@register("reboot_rds")
def restart_rds_instance(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    db_id = params.get("db_identifier", "")
    if not db_id:
        raise ValueError("restart_rds_instance requires db_identifier")

    rds = get_client("rds")
    logger.info("Rebooting RDS instance: %s", db_id)
    rds.reboot_db_instance(DBInstanceIdentifier=db_id)
    return ExecutionResult(success=True, message=f"Initiated reboot of RDS instance {db_id}.")


@register("restart_ec2_instance")
@register("reboot_ec2")
def restart_ec2_instance(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    instance_id = params.get("instance_id", "")
    if not instance_id:
        raise ValueError("restart_ec2_instance requires instance_id")

    ec2 = get_client("ec2")
    ec2.reboot_instances(InstanceIds=[instance_id])
    return ExecutionResult(success=True, message=f"Sent reboot to EC2 instance {instance_id}.")


@register("stop_start_ec2")
def stop_start_ec2(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    instance_id = params.get("instance_id", "")
    if not instance_id:
        raise ValueError("stop_start_ec2 requires instance_id")

    ec2 = get_client("ec2")
    ec2.stop_instances(InstanceIds=[instance_id])
    logger.info("Stopped EC2 instance %s, waiting for stopped state...", instance_id)
    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[instance_id], WaiterConfig={"MaxAttempts": 20, "Delay": 5})

    ec2.start_instances(InstanceIds=[instance_id])
    return ExecutionResult(success=True, message=f"Stop-started EC2 instance {instance_id}.")


@register("scale_ecs_service")
def scale_ecs_service(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    cluster = params.get("cluster_name", "")
    service = params.get("service_name", "")
    desired_count = int(params.get("desired_count", 2))
    if not cluster or not service:
        raise ValueError("scale_ecs_service requires cluster_name and service_name")

    ecs = get_client("ecs")
    ecs.update_service(cluster=cluster, service=service, desiredCount=desired_count)
    return ExecutionResult(success=True, message=f"Scaled ECS service {service} to {desired_count} tasks.")


@register("scale_out_ecs")
def scale_out_ecs(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    cluster = params.get("cluster_name", "")
    service = params.get("service_name", "")
    if not cluster or not service:
        raise ValueError("scale_out_ecs requires cluster_name and service_name")

    ecs = get_client("ecs")
    desc = ecs.describe_services(cluster=cluster, services=[service])
    current = desc["services"][0]["desiredCount"]
    new_count = current + max(1, current // 2)
    ecs.update_service(cluster=cluster, service=service, desiredCount=new_count)
    return ExecutionResult(success=True, message=f"Scaled out ECS service {service} from {current} to {new_count} tasks.")


@register("restart_lambda")
@register("clear_lambda_error")
def restart_lambda_function(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    fn_name = params.get("function_name", "")
    if not fn_name:
        raise ValueError("restart_lambda requires function_name")

    lam = get_client("lambda")
    existing = lam.get_function_configuration(FunctionName=fn_name)
    env_vars = existing.get("Environment", {}).get("Variables", {})
    env_vars["_RESTART_TS"] = str(int(time.time()))
    lam.update_function_configuration(FunctionName=fn_name, Environment={"Variables": env_vars})
    return ExecutionResult(success=True, message=f"Lambda function {fn_name} restarted (cold start forced).")


@register("retry_step_function")
def retry_step_function(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    sm_arn = params.get("state_machine_arn", "")
    input_str = params.get("execution_input", "{}")
    if not sm_arn:
        raise ValueError("retry_step_function requires state_machine_arn")

    sfn = get_client("stepfunctions")
    resp = sfn.start_execution(stateMachineArn=sm_arn, input=input_str)
    return ExecutionResult(success=True, message=f"Retried Step Functions execution: {resp['executionArn']}.")


@register("retry_glue_job")
def retry_glue_job(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    """
    NOTE: this polls Glue every 15s until the job reaches a terminal
    state, same as the reference implementation. For a long-running Glue
    job this will block the calling process (main.py's poll loop, or the
    approval_server request) for that entire duration - acceptable for a
    small deployment, but consider running this specific action in a
    background thread/worker if your Glue jobs run for many minutes.
    """
    job_name = params.get("job_name", "")
    if not job_name:
        raise ValueError("retry_glue_job requires job_name")

    glue = get_client("glue")
    logger.info("Retrying Glue job: %s", job_name)
    resp = glue.start_job_run(JobName=job_name)
    run_id = resp["JobRunId"]
    logger.info("Glue job started: job=%s run_id=%s - polling until terminal state", job_name, run_id)

    terminal_states = {"SUCCEEDED", "FAILED", "TIMEOUT", "STOPPED", "ERROR"}
    while True:
        time.sleep(15)
        run = glue.get_job_run(JobName=job_name, RunId=run_id)
        state = run["JobRun"]["JobRunState"]
        logger.info("Glue job %s state: %s", job_name, state)

        if state == "SUCCEEDED":
            return ExecutionResult(success=True, message=f"Glue job '{job_name}' (run {run_id}) completed successfully.")
        if state in terminal_states:
            error_msg = run["JobRun"].get("ErrorMessage", "No error message available")
            raise RuntimeError(f"Glue job '{job_name}' ended with state {state}: {error_msg}")


@register("manual_review")
def manual_review_placeholder(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    logger.warning("manual_review action reached the executor - this should have gone to human approval instead.")
    return ExecutionResult(success=False, message="manual_review is not an executable action - route to a human.")


@register("move_keyword_files_to_input")
def move_keyword_files_to_input(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    """
    SOP-EAM-MMSO-701 (Enrollments MMS job): moves keyword files pending in
    the keyword bucket's error/ folder back to input/ so the job
    reprocesses them on its next run. keyword_bucket is extracted by the
    Job Remediation Agent from the matched SOP's block, not guessed.
    """
    bucket = params.get("keyword_bucket", "")
    if not bucket:
        raise ValueError("move_keyword_files_to_input requires keyword_bucket")

    s3_client = S3Client(get_settings().s3)
    logger.info("Moving keyword files back to input/ in bucket %s", bucket)
    moved = s3_client.move_objects(bucket, source_prefix="error/", dest_prefix="input/")
    return ExecutionResult(
        success=True,
        message=f"Moved {len(moved)} file(s) from '{bucket}/error/' to '{bucket}/input/'.",
    )


@register("no_action_required")
def no_action_required(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    """
    SOP-EAM-MMSO-701 (Enrollments MMS job): a legitimate, intentional
    "nothing to do" outcome (early-stage failure with no keyword files
    pending) - unlike manual_review_placeholder above, this IS a real,
    successful auto-resolution and reports success=True.
    """
    return ExecutionResult(
        success=True,
        message="No remediation action required - failure occurred below the step threshold with no "
                 "keyword files pending in the error folder.",
    )


@register("export_eligible_member_hic")
@register("flag_member_ineligible")
def run_member_validation_job(params: dict, ticket: Ticket, recommendation) -> ExecutionResult:
    """
    SOP-EAM-MEMVALID-801 (and any other SOP declaring these actions): the
    LLM only decides THAT this SOP's job should run for this ticket - it
    cannot read kwd file content or query Postgres itself. The actual work
    (parsing every kwd file in the SOP's keyword_bucket, validating each
    referenced member against the eam/facets tables, writing eligible
    members' HIC to the bucket's input/ prefix) is the exact same
    deterministic code used when running the job directly or via --watch -
    see jobs/process_eam_member_kwd_files.py. Both action names route here
    since the pass/fail decision for each member is made internally, per
    member, by that job - not chosen up front by the LLM.
    """
    sop_id = recommendation.sop_id
    if not sop_id:
        raise ValueError(f"{recommendation.action} requires a matched SOP (recommendation.sop_id is empty)")

    from jobs.process_eam_member_kwd_files import process_sop

    sop_store = SOPStore(get_settings().remediation.sop_dir)
    sop = sop_store.get(sop_id)
    if not sop or not sop.keyword_bucket:
        raise ValueError(f"SOP '{sop_id}' has no keyword_bucket configured - nothing to run")

    eligible_hics = process_sop(sop)
    return ExecutionResult(
        success=True,
        message=(
            f"Ran {sop.sop_id} against s3://{sop.keyword_bucket}/{sop.error_prefix} - "
            f"{len(eligible_hics)} eligible member(s) exported across per-file "
            f"'{sop.output_filename}_<timestamp>.txt' object(s) in "
            f"s3://{sop.keyword_bucket}/{sop.input_prefix}."
        ),
    )
