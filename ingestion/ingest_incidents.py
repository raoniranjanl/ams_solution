"""
One-time ingestion script.

Reads ALL (resolved/closed, by default) ServiceNow incidents once via the
Table API and stores them - embedded with BGE-M3 - in the shared Qdrant
vector database. The Incident Router AI Agent later searches this data
to find similar past incidents when routing new ones.

Usage:
    python -m ingestion.ingest_incidents                 # resolved+closed only
    python -m ingestion.ingest_incidents --all            # include all states
    python -m ingestion.ingest_incidents --limit 10000
    python -m ingestion.ingest_incidents --recreate       # drop & recreate collection
"""
import argparse
import logging
import sys

from common.servicenow_client import ServiceNowClient
from common.vector_db import VectorStore
from config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def build_embedding_text(raw: dict) -> str:
    short_desc = raw.get("short_description", "") or ""
    desc = raw.get("description", "") or ""
    ci_name = raw.get("cmdb_ci.name") or (raw.get("cmdb_ci", {}) or {}).get("display_value") or ""
    parts = [short_desc, desc, f"Configuration Item: {ci_name}" if ci_name else ""]
    return "\n".join(p for p in parts if p).strip()


def raw_to_record(raw: dict) -> dict:
    assignment_group_name = raw.get("assignment_group.name") or (
        raw.get("assignment_group", {}) or {}
    ).get("display_value") or ""
    ci_name = raw.get("cmdb_ci.name") or (raw.get("cmdb_ci", {}) or {}).get("display_value")
    return {
        "number": raw.get("number", ""),
        "short_description": raw.get("short_description", "") or "",
        "description": raw.get("description", "") or "",
        "cmdb_ci_name": ci_name,
        "assignment_group": assignment_group_name,
        "close_notes": raw.get("close_notes", "") or "",
        "embedding_text": build_embedding_text(raw),
        "source": "incident",
    }


def main():
    parser = argparse.ArgumentParser(description="One-time ingestion of ServiceNow incidents into Qdrant.")
    parser.add_argument("--all", action="store_true", help="Include all incident states, not just resolved/closed.")
    parser.add_argument("--limit", type=int, default=5000, help="Max number of incidents to pull.")
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate the Qdrant collection first.")
    parser.add_argument("--batch-size", type=int, default=64, help="Embedding/upsert batch size.")
    args = parser.parse_args()

    settings = get_settings()

    # --- ServiceNow connection uses placeholder credentials until you set
    # SERVICENOW_USERNAME / SERVICENOW_PASSWORD in your environment. -----
    sn_client = ServiceNowClient(settings.servicenow)

    vector_store = VectorStore(settings.qdrant, settings.embedding)
    vector_store.ensure_collection(recreate=args.recreate)

    logger.info("Fetching historical incidents from ServiceNow (resolved_only=%s, limit=%s)...",
                not args.all, args.limit)
    raw_incidents = sn_client.fetch_all_incidents_for_training(limit=args.limit, resolved_only=not args.all)
    logger.info("Fetched %d raw incidents.", len(raw_incidents))

    if not raw_incidents:
        logger.warning("No incidents fetched - nothing to ingest. Check ServiceNow connectivity/credentials.")
        sys.exit(0)

    records = [raw_to_record(r) for r in raw_incidents]
    # skip records with essentially no text to embed
    records = [r for r in records if r["embedding_text"].strip()]

    total = 0
    for i in range(0, len(records), args.batch_size):
        batch = records[i:i + args.batch_size]
        vector_store.upsert_incidents(batch)
        total += len(batch)
        logger.info("Ingested %d/%d records...", total, len(records))

    logger.info("Ingestion complete. %d incidents stored in Qdrant collection '%s'.",
                total, settings.qdrant.collection)


if __name__ == "__main__":
    main()
