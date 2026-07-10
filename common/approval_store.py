"""
Persistent store for human-approval tokens - PostgreSQL-backed (shares one
Postgres instance/database with common/audit_store.py - see
config.PostgresSettings), so the orchestrator process and the
approval_server.py web process can share state via a real server, even
though they run as separate processes.

Handles TWO kinds of approvals through the same table/token mechanism:
  - "routing"     - low-confidence L2 assignment group suggestion
                    (Incident Router AI Agent)
  - "remediation" - a remediation action that failed guardrails and
                    needs a human OK before it's executed against AWS
                    (Job Remediation AI Agent)

The token is embedded in the approval link sent by email. When the human
clicks it, approval_server.py looks the token up here, checks `kind`, and
either updates the ServiceNow assignment group or executes the
remediation action (including its action_parameters, e.g. the ECS
cluster/service name, RDS identifier, etc.) accordingly.
"""
import contextlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import psycopg2
import psycopg2.extras


@dataclass
class ApprovalRecord:
    token: str
    kind: str  # "routing" | "remediation"
    ticket_number: str
    sys_id: str
    table: str
    confidence: float
    rationale: str
    short_description: str
    description: str
    cmdb_ci_name: Optional[str]
    status: str  # "pending" | "approved" | "rejected" | "expired"
    created_at: str
    expires_at: str
    # routing-specific
    assignment_group: Optional[str] = None
    # remediation-specific
    action: Optional[str] = None
    action_parameters: Dict[str, str] = field(default_factory=dict)
    job_name: Optional[str] = None
    sop_id: Optional[str] = None
    risk_level: Optional[str] = None


# Columns added after the original table shape - kept as a flat list since
# Postgres supports "ADD COLUMN IF NOT EXISTS" natively (no need to probe
# existing columns first the way SQLite's PRAGMA table_info required).
_OPTIONAL_COLUMNS = (
    ("kind", "TEXT DEFAULT 'routing'"),
    ("action", "TEXT"),
    ("action_parameters", "TEXT"),
    ("job_name", "TEXT"),
    ("sop_id", "TEXT"),
    ("risk_level", "TEXT"),
    ("short_description", "TEXT"),
    ("description", "TEXT"),
    ("cmdb_ci_name", "TEXT"),
)


class ApprovalStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._init_db()

    def _connect(self):
        return psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def _init_db(self) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approvals (
                        token TEXT PRIMARY KEY,
                        ticket_number TEXT NOT NULL,
                        sys_id TEXT NOT NULL,
                        table_name TEXT NOT NULL,
                        assignment_group TEXT,
                        confidence REAL NOT NULL,
                        rationale TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                    """
                )
                for col, coltype in _OPTIONAL_COLUMNS:
                    cur.execute(f"ALTER TABLE approvals ADD COLUMN IF NOT EXISTS {col} {coltype}")
            conn.commit()

    def create_approval(
        self, ticket_number: str, sys_id: str, table: str, confidence: float, rationale: str,
        kind: str = "routing",
        assignment_group: Optional[str] = None,
        action: Optional[str] = None, action_parameters: Optional[Dict[str, str]] = None,
        job_name: Optional[str] = None,
        sop_id: Optional[str] = None, risk_level: Optional[str] = None,
        short_description: str = "", description: str = "", cmdb_ci_name: Optional[str] = None,
        ttl_hours: int = 72,
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=ttl_hours)
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO approvals
                        (token, kind, ticket_number, sys_id, table_name, assignment_group,
                         action, action_parameters, job_name, sop_id, risk_level,
                         confidence, rationale, short_description, description, cmdb_ci_name,
                         status, created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                    """,
                    (token, kind, ticket_number, sys_id, table, assignment_group,
                     action, json.dumps(action_parameters or {}), job_name, sop_id, risk_level,
                     confidence, rationale, short_description, description, cmdb_ci_name,
                     now.isoformat(), expires.isoformat()),
                )
            conn.commit()
        return token

    def get(self, token: str) -> Optional[ApprovalRecord]:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM approvals WHERE token = %s", (token,))
                row = cur.fetchone()
        if not row:
            return None
        try:
            action_params = json.loads(row["action_parameters"]) if row["action_parameters"] else {}
        except (json.JSONDecodeError, TypeError):
            action_params = {}
        return ApprovalRecord(
            token=row["token"],
            kind=row["kind"] or "routing",
            ticket_number=row["ticket_number"],
            sys_id=row["sys_id"],
            table=row["table_name"],
            assignment_group=row["assignment_group"],
            action=row["action"],
            action_parameters=action_params,
            job_name=row["job_name"],
            sop_id=row["sop_id"],
            risk_level=row["risk_level"],
            confidence=row["confidence"],
            rationale=row["rationale"] or "",
            short_description=row["short_description"] or "",
            description=row["description"] or "",
            cmdb_ci_name=row["cmdb_ci_name"],
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def is_expired(self, record: ApprovalRecord) -> bool:
        expires_at = datetime.fromisoformat(record.expires_at)
        return datetime.now(timezone.utc) > expires_at

    def mark_status(self, token: str, status: str) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE approvals SET status = %s WHERE token = %s", (status, token))
            conn.commit()
