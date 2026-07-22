"""
Approval web server.

Handles clicks on the Approve/Reject links sent by email. Two kinds of
approvals share this same server:

  - kind="routing"     - low-confidence L2 assignment suggestion
                         (Incident Router AI Agent). Approve -> updates
                         the ServiceNow assignment_group.
  - kind="remediation" - a remediation action that failed one or more
                         guardrail checks (Job Remediation AI Agent).
                         Approve -> actually executes the action via
                         RemediationExecutor. Reject -> leaves it for
                         manual remediation.

Every outcome is written to the audit trail, and successful outcomes are
fed back into the shared vector DB for continuous learning - the same as
the auto-approved paths in ams_orchestrator_agent.py.

Run alongside the orchestrator:
    python approval_server.py
(defaults to http://0.0.0.0:8000 - configurable via APPROVAL_SERVER_HOST /
APPROVAL_SERVER_PORT in .env; APPROVAL_BASE_URL must point at wherever
this process is actually reachable from the email recipient's network.)
"""
import logging

from flask import Flask, request

from datetime import datetime

from common.approval_store import ApprovalStore
from common.audit_store import AuditStore
from common.remediation_executor import RemediationExecutor
from common.roster_client import DomainResolver, RosterClient
from common.servicenow_client import ServiceNowClient
from common.vector_db import VectorStore
from config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()
servicenow_client = ServiceNowClient(settings.servicenow)
approval_store = ApprovalStore(settings.postgres.url)
vector_store = VectorStore(settings.qdrant, settings.embedding)
remediation_executor = RemediationExecutor()
audit_store = AuditStore(settings.postgres.url)

domain_resolver = DomainResolver(settings.roster.group_domain_map)
roster_client = None
if settings.roster.enabled:
    try:
        roster_client = RosterClient(settings.roster.file_path, settings.roster.sheet_name)
    except Exception:
        logger.exception(
            "Failed to load roster file '%s'; approved routings will set the "
            "assignment group only (no auto Assigned-to).", settings.roster.file_path,
        )


def _resolve_shift_assignee(record) -> str | None:
    """Same roster lookup as the orchestrator's auto-approve path, applied
    to a human-approved routing record (which carries sys_created_on via
    the ticket's raw creation time captured at approval-request time)."""
    if not roster_client:
        return None
    domain = domain_resolver.resolve(record.assignment_group)
    if not domain:
        return None
    created_on = getattr(record, "sys_created_on", None)
    if not created_on:
        logger.warning("Approval record for %s has no sys_created_on; skipping roster auto-assign.",
                        record.ticket_number)
        return None
    try:
        created_at = datetime.strptime(created_on, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.warning("Could not parse sys_created_on '%s' for %s; skipping roster auto-assign.",
                        created_on, record.ticket_number)
        return None
    return roster_client.get_assignee(domain, created_at)

app = Flask(__name__)


def _page(title: str, message: str, ok: bool = True) -> str:
    color = "#2e7d32" if ok else "#c62828"
    return f"""\
<html>
  <body style="font-family: Arial, sans-serif; padding: 40px; text-align: center;">
    <h2 style="color: {color};">{title}</h2>
    <p style="font-size: 16px; color: #333;">{message}</p>
  </body>
</html>
"""


def _approve_routing(record) -> str:
    shift_assignee = _resolve_shift_assignee(record)
    servicenow_client.update_assignment_group(
        record.sys_id, record.assignment_group, table=record.table, assigned_to=shift_assignee,
    )
    note = (
        f"[AMS AI] Human approved suggested L2 group '{record.assignment_group}' via email link "
        f"(original confidence={record.confidence:.2f}). Assignment applied."
    )
    if shift_assignee:
        note += f" Assigned to on-shift resource '{shift_assignee}' per weekly roster."
    servicenow_client.add_work_note(record.sys_id, note, table=record.table)
    logger.info("Approved routing for %s -> %s via email link", record.ticket_number, record.assignment_group)

    try:
        vector_store.upsert_routing_feedback(
            number=record.ticket_number,
            short_description=record.short_description,
            description=record.description,
            cmdb_ci_name=record.cmdb_ci_name,
            assignment_group=record.assignment_group,
        )
    except Exception:
        logger.exception("Failed to feed human-approved routing for %s back into the vector DB",
                          record.ticket_number)

    assignee_note = f" and <strong>{shift_assignee}</strong> (on-shift resource)" if shift_assignee else ""
    return f"Ticket {record.ticket_number} has been assigned to <strong>{record.assignment_group}</strong>{assignee_note}."


def _approve_remediation(record) -> str:
    # Build a minimal ticket-like object with just what the executor needs.
    from common.models import RemediationRecommendation, Ticket

    ticket = Ticket(
        sys_id=record.sys_id, number=record.ticket_number, table=record.table,
        short_description=record.short_description, description=record.description,
        cmdb_ci_name=record.cmdb_ci_name,
    )
    recommendation = RemediationRecommendation(
        ticket_number=record.ticket_number, job_name=record.job_name, action=record.action,
        action_parameters=record.action_parameters,
        confidence=record.confidence, risk_level=record.risk_level, rationale=record.rationale,
        requires_human_approval=False, sop_id=record.sop_id,
    )

    exec_result = remediation_executor.execute(ticket, recommendation)
    note = (
        f"[AMS AI] Human approved remediation action '{record.action}' via email link "
        f"(original confidence={record.confidence:.2f}). "
        f"Result: {'SUCCESS' if exec_result.success else 'FAILED'} - {exec_result.message}"
    )
    servicenow_client.add_work_note(record.sys_id, note, table=record.table)

    audit_store.log(
        ticket_number=record.ticket_number, actor="human_approved",
        result="success" if exec_result.success else "failed", message=exec_result.message,
        job_name=record.job_name, action=record.action, sop_id=record.sop_id,
        risk_level=record.risk_level, confidence=record.confidence,
    )

    if exec_result.success:
        try:
            vector_store.upsert_job_remediation_feedback(
                number=record.ticket_number,
                short_description=record.short_description,
                description=record.description,
                cmdb_ci_name=record.cmdb_ci_name,
                action_taken=record.action,
                outcome="success (human-approved)",
            )
        except Exception:
            logger.exception("Failed to feed human-approved remediation for %s into vector DB",
                              record.ticket_number)

    logger.info("Approved remediation for %s -> %s via email link (success=%s)",
                record.ticket_number, record.action, exec_result.success)
    if not exec_result.success:
        raise RuntimeError(exec_result.message)
    return f"Action <strong>{record.action}</strong> executed for ticket {record.ticket_number}."


@app.route("/approve", methods=["GET"])
def approve():
    token = request.args.get("token", "")
    record = approval_store.get(token)

    if not record:
        return _page("Link not recognized", "This approval link is invalid.", ok=False), 404

    if record.status != "pending":
        return _page(
            "Already processed",
            f"Ticket {record.ticket_number} was already marked '{record.status}'. No action taken.",
            ok=(record.status == "approved"),
        )

    if approval_store.is_expired(record):
        approval_store.mark_status(token, "expired")
        return _page(
            "Link expired",
            f"This approval link for {record.ticket_number} has expired. Please act manually in ServiceNow.",
            ok=False,
        )

    try:
        if record.kind == "remediation":
            message = _approve_remediation(record)
        else:
            message = _approve_routing(record)
        approval_store.mark_status(token, "approved")
        return _page("Approved", message, ok=True)
    except Exception:
        logger.exception("Failed to apply approved action for %s", record.ticket_number)
        return _page(
            "Something went wrong",
            f"Could not complete the action for {record.ticket_number}. Please act manually "
            f"and check the server logs.",
            ok=False,
        ), 500


@app.route("/reject", methods=["GET"])
def reject():
    token = request.args.get("token", "")
    record = approval_store.get(token)

    if not record:
        return _page("Link not recognized", "This link is invalid.", ok=False), 404
    if record.status != "pending":
        return _page("Already processed", f"Ticket {record.ticket_number} was already '{record.status}'.")

    approval_store.mark_status(token, "rejected")
    note_subject = record.action if record.kind == "remediation" else record.assignment_group
    try:
        servicenow_client.add_work_note(
            record.sys_id,
            f"[AMS AI] Human rejected suggested {record.kind} '{note_subject}' via email link. "
            f"Needs manual handling.",
            table=record.table,
        )
    except Exception:
        logger.exception("Failed to add rejection work note for %s", record.ticket_number)

    if record.kind == "remediation":
        audit_store.log(
            ticket_number=record.ticket_number, actor="human_rejected", result="rejected",
            message="Human rejected the proposed remediation action via email link.",
            job_name=record.job_name, action=record.action, sop_id=record.sop_id,
            risk_level=record.risk_level, confidence=record.confidence,
        )

    return _page("Rejected", f"Ticket {record.ticket_number} was left unassigned for manual handling.")


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    logger.info("Starting approval server on %s:%s", settings.approval.host, settings.approval.port)
    app.run(host=settings.approval.host, port=settings.approval.port)
