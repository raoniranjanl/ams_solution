"""
One-time ingestion script for SOP documents.

Loads every SOP in sop_documents/ (plus their .txt narratives) via
SOPStore and indexes them into the shared Qdrant collection (source="sop")
so the Job Remediation AI Agent can retrieve them semantically, the same
way ingestion/ingest_incidents.py seeds historical incidents for the
Incident Router AI Agent.

Usage:
    python -m ingestion.ingest_sops
"""
import logging

from common.sop_store import SOPStore
from common.vector_db import VectorStore
from config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    settings = get_settings()

    vector_store = VectorStore(settings.qdrant, settings.embedding)
    vector_store.ensure_collection()

    sop_store = SOPStore(settings.remediation.sop_dir, vector_store=vector_store)
    sops = sop_store.all()
    if not sops:
        logger.warning(
            "No SOPs found in '%s'. Check SOP_DOCUMENTS_DIR in your .env.", settings.remediation.sop_dir
        )
        return

    logger.info("Loaded %d SOP(s):", len(sops))
    for sop in sops:
        flag = "auto-resolvable" if sop.auto_resolvable else "human-approval-only"
        logger.info("  - %s (%s, risk=%s, %s)", sop.sop_id, sop.service or "?", sop.risk_level, flag)

    sop_store.index_into_vector_db()
    logger.info("Done. %d SOP(s) indexed into Qdrant collection '%s' (source=sop).",
                len(sops), settings.qdrant.collection)


if __name__ == "__main__":
    main()
