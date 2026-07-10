"""
Shared data models passed between agents.
"""
from typing import Dict, Optional, List
from pydantic import BaseModel, Field


class Ticket(BaseModel):
    """Normalized representation of a ServiceNow record (incident or
    service catalog request/item)."""

    sys_id: str
    number: str
    table: str  # "incident" or "sc_req_item" / "sc_request"
    sys_class_name: Optional[str] = None
    short_description: str = ""
    description: str = ""
    cmdb_ci: Optional[str] = None
    cmdb_ci_name: Optional[str] = None
    assignment_group: Optional[str] = None
    priority: Optional[str] = None
    state: Optional[str] = None
    raw: dict = Field(default_factory=dict)  # original ServiceNow payload

    def to_embedding_text(self) -> str:
        parts = [
            self.short_description or "",
            self.description or "",
            f"Configuration Item: {self.cmdb_ci_name}" if self.cmdb_ci_name else "",
        ]
        return "\n".join(p for p in parts if p).strip()


class SimilarIncident(BaseModel):
    """A historical incident retrieved from the vector database."""

    number: str
    short_description: str
    assignment_group: str
    close_notes: Optional[str] = None
    score: float


class RoutingResult(BaseModel):
    """Output of the Incident Router AI Agent."""

    ticket_number: str
    assignment_group: Optional[str] = None
    confidence: float
    rationale: str
    insufficient_information: bool = False
    similar_incidents: List[SimilarIncident] = Field(default_factory=list)


class JobFailureAssessment(BaseModel):
    """Output of the AMS Orchestrator's job-failure classification step."""

    ticket_number: str
    is_job_failure: bool
    confidence: float
    rationale: str
    job_name: Optional[str] = None


class SimilarJobFailure(BaseModel):
    """A historical job-failure record retrieved from the vector database."""

    number: str
    short_description: str
    action_taken: str
    outcome: Optional[str] = None
    score: float


class RemediationRecommendation(BaseModel):
    """Output of the Job Remediation AI Agent."""

    ticket_number: str
    job_name: Optional[str] = None
    action: Optional[str] = None            # must match one of the matched SOP's resolution_steps
    action_parameters: Dict[str, str] = Field(default_factory=dict)  # e.g. {"cluster_name": "...", "service_name": "..."}
    confidence: float = 0.0
    risk_level: Optional[str] = None        # "LOW" | "MEDIUM" | "HIGH", from the matched SOP
    rationale: str = ""
    requires_human_approval: bool = True    # LLM's own opinion; guardrails can still override this
    insufficient_information: bool = False
    sop_id: Optional[str] = None
    sop_title: Optional[str] = None
    similar_job_failures: List[SimilarJobFailure] = Field(default_factory=list)


class GuardrailResult(BaseModel):
    """Output of the guardrails validator - the safety gate before auto-execution."""

    passed: bool
    failed_checks: List[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    """Outcome of actually running a remediation action."""

    success: bool
    message: str
