"""
Shared Qdrant vector store.

This single collection is used by BOTH the Incident Router AI Agent
(to find similar historical incidents for L2 routing) and, in future,
the Job Remediation AI Agent (to find similar historical job failures
and their remediation steps). Payload fields are kept generic enough
to support both use cases.

NOTE on qdrant-client API compatibility: newer qdrant-client releases
(>= ~1.10) removed QdrantClient.search() entirely in favor of
QdrantClient.query_points(). This module uses query_points() so it works
against current qdrant-client versions; if you're on an older client
that only has .search(), upgrade qdrant-client rather than the other
way around (query_points is the actively maintained API going forward).
"""
import logging
import uuid
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from common.embeddings import Embedder
from common.models import SimilarIncident
from config import EmbeddingSettings, QdrantSettings

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, qdrant_settings: QdrantSettings, embedding_settings: EmbeddingSettings):
        self.settings = qdrant_settings
        self.client = QdrantClient(url=qdrant_settings.url, api_key=qdrant_settings.api_key)
        self.embedder = Embedder(embedding_settings)
        self.collection = qdrant_settings.collection
        self.dim = embedding_settings.dim

    def ensure_collection(self, recreate: bool = False) -> None:
        exists = self.client.collection_exists(self.collection)
        if exists and not recreate:
            return
        if exists and recreate:
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=qmodels.VectorParams(size=self.dim, distance=qmodels.Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s' (dim=%s)", self.collection, self.dim)

    def upsert_incidents(self, records: List[Dict]) -> None:
        """
        records: list of dicts each with keys:
            number, short_description, description, cmdb_ci_name,
            assignment_group, close_notes, embedding_text
        """
        if not records:
            return
        texts = [r["embedding_text"] for r in records]
        vectors = self.embedder.encode(texts)

        points = []
        for record, vector in zip(records, vectors):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, record["number"]))
            payload = {
                "number": record.get("number"),
                "short_description": record.get("short_description"),
                "description": record.get("description", "")[:2000],
                "cmdb_ci_name": record.get("cmdb_ci_name"),
                "assignment_group": record.get("assignment_group"),
                "close_notes": (record.get("close_notes") or "")[:2000],
                "source": record.get("source", "incident"),
            }
            points.append(qmodels.PointStruct(id=point_id, vector=vector, payload=payload))

        self.client.upsert(collection_name=self.collection, points=points)
        logger.info("Upserted %d records into '%s'", len(points), self.collection)

    def upsert_routing_feedback(
        self,
        number: str,
        short_description: str,
        description: str,
        cmdb_ci_name: Optional[str],
        assignment_group: str,
        close_notes: str = "",
    ) -> None:
        """
        Continuous-learning feedback write. Call this once an incident has
        actually been assigned to an L2 group - either auto-assigned with
        high confidence, or approved by a human via the email approve
        link. This upserts that confirmed outcome back into the same
        collection the Incident Router AI Agent searches, so the next
        similar incident benefits from it immediately rather than only
        from the one-time historical ingestion. Safe to call repeatedly
        for the same incident number - it's an upsert, not an insert.
        """
        parts = [
            short_description or "",
            description or "",
            f"Configuration Item: {cmdb_ci_name}" if cmdb_ci_name else "",
        ]
        embedding_text = "\n".join(p for p in parts if p).strip()
        if not embedding_text or not assignment_group:
            logger.warning(
                "Skipping vector DB feedback write for %s - missing embedding text or assignment group.",
                number,
            )
            return

        record = {
            "number": number,
            "short_description": short_description,
            "description": description,
            "cmdb_ci_name": cmdb_ci_name,
            "assignment_group": assignment_group,
            "close_notes": close_notes,
            "embedding_text": embedding_text,
            "source": "incident",
        }
        self.upsert_incidents([record])
        logger.info("Fed confirmed routing for %s (-> %s) back into the vector DB.", number, assignment_group)

    def upsert_job_remediation_feedback(
        self,
        number: str,
        short_description: str,
        description: str,
        cmdb_ci_name: Optional[str],
        action_taken: str,
        outcome: str = "",
    ) -> None:
        """
        Continuous-learning feedback write for the Job Remediation AI
        Agent - same idea as upsert_routing_feedback, but tagged
        source="job_failure" so it's retrieved separately from L2-routing
        history. Reuses the "assignment_group" payload field to store the
        action taken and "close_notes" to store the outcome, so both
        agents share the exact same collection/schema shape.
        """
        parts = [
            short_description or "",
            description or "",
            f"Configuration Item: {cmdb_ci_name}" if cmdb_ci_name else "",
        ]
        embedding_text = "\n".join(p for p in parts if p).strip()
        if not embedding_text or not action_taken:
            logger.warning(
                "Skipping job-remediation vector DB feedback write for %s - missing text or action.",
                number,
            )
            return

        record = {
            "number": number,
            "short_description": short_description,
            "description": description,
            "cmdb_ci_name": cmdb_ci_name,
            "assignment_group": action_taken,  # reused field: "action taken" for job_failure records
            "close_notes": outcome,
            "embedding_text": embedding_text,
            "source": "job_failure",
        }
        self.upsert_incidents([record])
        logger.info("Fed job-remediation outcome for %s (-> %s) back into the vector DB.", number, action_taken)

    def search(self, query_text: str, top_k: int = 5, source_filter: Optional[str] = None) -> List[SimilarIncident]:
        vector = self.embedder.encode_one(query_text)
        qfilter = None
        if source_filter:
            qfilter = qmodels.Filter(
                must=[qmodels.FieldCondition(key="source", match=qmodels.MatchValue(value=source_filter))]
            )

        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        )
        hits = response.points

        results = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                SimilarIncident(
                    number=payload.get("number", ""),
                    short_description=payload.get("short_description", ""),
                    assignment_group=payload.get("assignment_group", "") or "UNKNOWN",
                    close_notes=payload.get("close_notes"),
                    score=hit.score,
                )
            )
        return results
