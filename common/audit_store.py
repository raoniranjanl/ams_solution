"""
Audit trail for remediation actions - analogous to the reference
architecture's Zone 4 DynamoDB audit trail, implemented in PostgreSQL
(shares one Postgres instance/database with common/approval_store.py -
see config.PostgresSettings).

Every remediation decision gets one row here, whether it was
auto-executed or human-approved, and whether it succeeded or failed -
this is the record you'd point an auditor at.
"""
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import psycopg2
import psycopg2.extras


@dataclass
class AuditRecord:
    id: int
    ticket_number: str
    job_name: Optional[str]
    action: Optional[str]
    sop_id: Optional[str]
    risk_level: Optional[str]
    confidence: float
    actor: str          # "ai_auto" | "human_approved" | "human_rejected"
    result: str         # "success" | "failed" | "pending_approval" | "rejected"
    message: str
    created_at: str


class AuditStore:
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
                    CREATE TABLE IF NOT EXISTS audit_trail (
                        id SERIAL PRIMARY KEY,
                        ticket_number TEXT NOT NULL,
                        job_name TEXT,
                        action TEXT,
                        sop_id TEXT,
                        risk_level TEXT,
                        confidence REAL,
                        actor TEXT NOT NULL,
                        result TEXT NOT NULL,
                        message TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            conn.commit()

    def log(
        self, ticket_number: str, actor: str, result: str, message: str = "",
        job_name: Optional[str] = None, action: Optional[str] = None,
        sop_id: Optional[str] = None, risk_level: Optional[str] = None, confidence: float = 0.0,
    ) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_trail
                        (ticket_number, job_name, action, sop_id, risk_level, confidence,
                         actor, result, message, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (ticket_number, job_name, action, sop_id, risk_level, confidence,
                     actor, result, message, datetime.now(timezone.utc).isoformat()),
                )
            conn.commit()

    def for_ticket(self, ticket_number: str) -> List[AuditRecord]:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM audit_trail WHERE ticket_number = %s ORDER BY created_at", (ticket_number,)
                )
                rows = cur.fetchall()
        return [
            AuditRecord(
                id=r["id"], ticket_number=r["ticket_number"], job_name=r["job_name"], action=r["action"],
                sop_id=r["sop_id"], risk_level=r["risk_level"], confidence=r["confidence"],
                actor=r["actor"], result=r["result"], message=r["message"], created_at=r["created_at"],
            )
            for r in rows
        ]
