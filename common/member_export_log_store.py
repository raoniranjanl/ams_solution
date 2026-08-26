"""
Persistent "already exported" ledger for jobs/process_eam_member_kwd_files.py
(see sql/create_member_export_log_table.sql). Once a member has been
exported to a Legacy_<timestamp>.txt output object, they are never
re-exported on a later run - this table is what makes that durable across
process restarts (a plain in-memory set would reset every run).

Mirrors the raw-psycopg2 connection pattern used by common/audit_store.py
and common/approval_store.py, including owning its own table creation.
"""
import contextlib
from typing import Optional

import psycopg2
import psycopg2.extras


class MemberExportLogStore:
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
                    CREATE TABLE IF NOT EXISTS member_export_log (
                        id SERIAL PRIMARY KEY,
                        pmeme_health_id VARCHAR(64) NOT NULL UNIQUE,
                        exported_value VARCHAR(64) NOT NULL,
                        source_key VARCHAR(255),
                        sop_id VARCHAR(64),
                        exported_at TIMESTAMP NOT NULL DEFAULT now()
                    )
                    """
                )
            conn.commit()

    def has_been_exported(self, pmeme_health_id: str) -> bool:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM member_export_log WHERE pmeme_health_id = %s",
                    (pmeme_health_id,),
                )
                return cur.fetchone() is not None

    def record_export(
        self, pmeme_health_id: str, exported_value: str,
        source_key: Optional[str] = None, sop_id: Optional[str] = None,
    ) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO member_export_log (pmeme_health_id, exported_value, source_key, sop_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (pmeme_health_id) DO NOTHING
                    """,
                    (pmeme_health_id, exported_value, source_key, sop_id),
                )
            conn.commit()
