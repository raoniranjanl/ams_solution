"""
Audit trail for remediation actions - analogous to the reference
architecture's Zone 4 DynamoDB audit trail, implemented in SQLite (stdlib,
no extra dependency, same pattern as common/approval_store.py) so it's
easy to run locally and easy to swap for a real database later.

Every remediation decision gets one row here, whether it was
auto-executed or human-approved, and whether it succeeded or failed -
this is the record you'd point an auditor at.
"""
import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


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
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_trail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            conn.execute(
                """
                INSERT INTO audit_trail
                    (ticket_number, job_name, action, sop_id, risk_level, confidence,
                     actor, result, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticket_number, job_name, action, sop_id, risk_level, confidence,
                 actor, result, message, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def for_ticket(self, ticket_number: str) -> List[AuditRecord]:
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM audit_trail WHERE ticket_number = ? ORDER BY created_at", (ticket_number,)
            ).fetchall()
        return [
            AuditRecord(
                id=r["id"], ticket_number=r["ticket_number"], job_name=r["job_name"], action=r["action"],
                sop_id=r["sop_id"], risk_level=r["risk_level"], confidence=r["confidence"],
                actor=r["actor"], result=r["result"], message=r["message"], created_at=r["created_at"],
            )
            for r in rows
        ]
