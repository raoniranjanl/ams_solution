"""
Read-only lookups against the eam and facets member tables
(see sql/create_eam_facets_tables.sql), used by
jobs/process_eam_member_kwd_files.py per SOP-EAM-MEMVALID-801.

Mirrors the raw-psycopg2 connection pattern used by common/audit_store.py
and common/approval_store.py - no ORM, plain SELECT by natural key.
"""
import contextlib
from typing import Dict, Optional

import psycopg2
import psycopg2.extras


class EamFacetsStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self):
        return psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def get_eam_by_member_id(self, member_id: str) -> Optional[Dict]:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM eam WHERE member_id = %s", (member_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_facets_by_mem_health_id(self, mem_health_id: str) -> Optional[Dict]:
        with contextlib.closing(self._connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM facets WHERE mem_health_id = %s", (mem_health_id,))
                row = cur.fetchone()
                return dict(row) if row else None
