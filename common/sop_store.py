"""
SOP (Standard Operating Procedure) store for the Job Remediation AI Agent.

Loads the real SOP library (sop_documents/*.json + matching *.txt, plus
sop_documents/runbooks/*.json) and makes it available two ways:
  1. Structured metadata (resolution_steps, auto_resolvable, risk_level,
     service, error_type) used by common/guardrails.py to decide whether
     an action may auto-execute.
  2. Full-text content (the richer .txt narrative version when present,
     else the JSON description) indexed into the SAME shared Qdrant
     collection as the Incident Router AI Agent, tagged source="sop" -
     so "one vector database for all AI agents" holds, and the Job
     Remediation Agent's RAG step reuses the existing BGE-M3 + Qdrant
     infrastructure.

SCHEMA NOTE: the real SOP files are not all in the same shape:
  - SOP-EC2/RDS/LAMBDA-*.json use a rich schema with resolution_steps,
    auto_resolvable, risk_level, decision_tree, escalation_contact.
  - SOP-GLUE-501.json uses an older/simpler schema (steps, rollback,
    escalation, prerequisites) with NO resolution_steps/auto_resolvable/
    risk_level fields at all.
  - SOP-SFN-601 has only a .txt narrative, no .json at all.
This loader does NOT guess at missing structured fields. Anything without
an explicit "resolution_steps" list is loaded with auto_resolvable=False,
risk_level="HIGH", resolution_steps=[] - which means guardrails will
always route it to a human (fail-closed, matching the project's
anti-hallucination stance) until you add the missing fields yourself.
SOP-SFN-601 (no .json) is indexed for RAG context but has no guardrail
metadata, so any job matched to it also always requires human approval.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SOP:
    sop_id: str
    title: str
    service: str = ""
    error_type: str = ""
    description: str = ""
    resolution_steps: List[str] = field(default_factory=list)
    auto_resolvable: bool = False
    risk_level: str = "HIGH"
    blocked_actions: List[str] = field(default_factory=list)
    escalation_contact: str = ""
    feedback_notes: str = ""
    job_name: str = ""            # e.g. "agentic-dev-customer-etl-pipeline" - CloudWatch lookup hint
    log_group: str = ""           # optional explicit CloudWatch log group name/prefix override
    full_text: str = ""           # rich .txt content if present, else description
    keywords: List[str] = field(default_factory=list)  # derived, used for keyword matching
    match_rule: str = ""          # literal rule text (e.g. a log substring match) for the Job
                                   # Remediation Agent to apply verbatim, not just paraphrase
    decision_tree: List[dict] = field(default_factory=list)   # structured if/action/approval steps
    example_log_events: List[dict] = field(default_factory=list)  # worked examples of a rule match
    error_bucket: str = ""         # S3 bucket holding this job's run log files (grounding, not CloudWatch)
    keyword_bucket: str = ""       # S3 bucket with fixed input/processed/error folders of data files
    error_prefix: str = "error/"   # keyword_bucket prefix to read pending files from
    input_prefix: str = "input/"   # keyword_bucket prefix eligible/processed output goes to
    output_filename: str = "EAF"       # output filename PREFIX (under input_prefix) - the job appends
                                        # "_<YYYYMMDD_HHMMSS>.txt" per source file processed
    output_bucket: str = ""            # bucket the Legacy/output files are written to (input_prefix) -
                                        # empty = same bucket as keyword_bucket (read and write together)
    mms_bucket: str = ""               # optional: downstream bucket fully-matching members with
                                        # pending PCP changes get forwarded to (empty = feature off)
    mms_input_prefix: str = "input/"   # prefix under mms_bucket the forwarded kwd file is written to


class SOPStore:
    def __init__(self, sop_dir: str, vector_store=None):
        self.sop_dir = sop_dir
        self.vector_store = vector_store  # optional: same shared VectorStore, for RAG-based matching
        self._sops: Dict[str, SOP] = {}
        self._runbooks: Dict[str, dict] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not os.path.isdir(self.sop_dir):
            logger.warning("SOP directory '%s' does not exist - no SOPs loaded.", self.sop_dir)
            return

        # Every .txt is a candidate SOP narrative, keyed by its stem (e.g. "SOP-RDS-201").
        txt_by_stem = {}
        for fname in os.listdir(self.sop_dir):
            if fname.endswith(".txt"):
                stem = fname[:-4]
                with open(os.path.join(self.sop_dir, fname), "r") as f:
                    txt_by_stem[stem] = f.read()

        json_stems = set()
        for fname in sorted(os.listdir(self.sop_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.sop_dir, fname)
            stem = fname[:-5]
            json_stems.add(stem)
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                sop = self._parse_sop(data, full_text=txt_by_stem.get(stem, ""))
                self._sops[sop.sop_id] = sop
            except Exception:
                logger.exception("Failed to load SOP file %s", path)

        # .txt-only SOPs (no matching .json) - index for RAG, but with no
        # guardrail metadata, so they can never pass auto-execution checks.
        for stem, text in txt_by_stem.items():
            if stem in json_stems:
                continue
            sop_id, title, service, error_type = self._parse_txt_header(stem, text)
            sop = SOP(
                sop_id=sop_id, title=title, service=service, error_type=error_type,
                description="(text-only SOP, no structured metadata)",
                resolution_steps=[], auto_resolvable=False, risk_level="HIGH",
                full_text=text, keywords=self._derive_keywords(sop_id, title, service, error_type),
            )
            self._sops[sop_id] = sop
            logger.info("Loaded text-only SOP '%s' - no guardrail metadata, always requires human approval.", sop_id)

        # Runbooks referenced by decision_tree steps - loaded for optional
        # extra RAG context, not required for guardrail checks.
        runbook_dir = os.path.join(self.sop_dir, "runbooks")
        if os.path.isdir(runbook_dir):
            for fname in sorted(os.listdir(runbook_dir)):
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(runbook_dir, fname), "r") as f:
                        self._runbooks[fname] = json.load(f)
                except Exception:
                    logger.exception("Failed to load runbook %s", fname)

        logger.info("Loaded %d SOP(s) and %d runbook(s) from %s", len(self._sops), len(self._runbooks), self.sop_dir)

    def _parse_sop(self, data: dict, full_text: str) -> SOP:
        sop_id = data["sop_id"]
        title = data.get("title", sop_id)
        service = data.get("service", "")
        error_type = data.get("error_type", "")
        description = data.get("description", "")
        # Not every SOP file has these - default fail-closed (see module docstring).
        resolution_steps = data.get("resolution_steps", [])
        auto_resolvable = bool(data.get("auto_resolvable", False))
        risk_level = str(data.get("risk_level", "HIGH")).upper()
        blocked_actions = data.get("blocked_actions", [])
        escalation_contact = data.get("escalation_contact") or data.get("escalation", "")
        feedback_notes = data.get("feedback_notes", "")
        job_name = data.get("job_name", "")
        log_group = data.get("log_group", "")
        match_rule = data.get("match_rule", "")
        decision_tree = data.get("decision_tree", [])
        example_log_events = data.get("example_log_events", [])
        error_bucket = data.get("error_bucket", "")
        keyword_bucket = data.get("keyword_bucket", "")
        error_prefix = data.get("error_prefix", "error/")
        input_prefix = data.get("input_prefix", "input/")
        output_filename = data.get("output_filename", "EAF")
        output_bucket = data.get("output_bucket", "")
        mms_bucket = data.get("mms_bucket", "")
        mms_input_prefix = data.get("mms_input_prefix", "input/")

        return SOP(
            sop_id=sop_id, title=title, service=service, error_type=error_type,
            description=description, resolution_steps=resolution_steps,
            auto_resolvable=auto_resolvable, risk_level=risk_level,
            blocked_actions=blocked_actions, escalation_contact=escalation_contact,
            feedback_notes=feedback_notes, job_name=job_name, log_group=log_group,
            full_text=full_text or description,
            keywords=self._derive_keywords(sop_id, title, service, error_type),
            match_rule=match_rule, decision_tree=decision_tree, example_log_events=example_log_events,
            error_bucket=error_bucket, keyword_bucket=keyword_bucket,
            error_prefix=error_prefix, input_prefix=input_prefix, output_filename=output_filename,
            output_bucket=output_bucket, mms_bucket=mms_bucket, mms_input_prefix=mms_input_prefix,
        )

    @staticmethod
    def _derive_keywords(sop_id: str, title: str, service: str, error_type: str) -> List[str]:
        raw = f"{sop_id} {title} {service} {error_type}".lower()
        for ch in "-_/():":
            raw = raw.replace(ch, " ")
        return [w for w in raw.split() if len(w) > 2]

    @staticmethod
    def _parse_txt_header(stem: str, text: str) -> tuple:
        """
        Parses the "SOP ID / Service / Error" header block that appears at
        the top of the narrative .txt SOPs, e.g.:
            SOP ID   : SOP-SFN-601
            Service  : AWS Step Functions
            Error    : State Machine Execution Failed / Timed Out
        Falls back to sensible defaults if the header isn't in this shape.
        """
        sop_id, service, error_type = stem, "", ""
        for line in text.splitlines()[:10]:
            line = line.strip()
            if line.lower().startswith("sop id"):
                sop_id = line.split(":", 1)[-1].strip() or stem
            elif line.lower().startswith("service"):
                service = line.split(":", 1)[-1].strip()
            elif line.lower().startswith("error"):
                error_type = line.split(":", 1)[-1].strip()
        title = f"{service} - {error_type}" if service or error_type else stem
        return sop_id, title, service, error_type

    def index_into_vector_db(self) -> None:
        """
        Index SOPs (their full_text narrative) into the shared Qdrant
        collection (source="sop") so the Job Remediation Agent can find
        them via semantic search, same collection the Incident Router
        Agent uses. Safe to call repeatedly - it's an upsert. Also see
        ingestion/ingest_sops.py for a standalone CLI entry point.
        """
        if not self.vector_store or not self._sops:
            return
        records = []
        for sop in self._sops.values():
            embedding_text = sop.full_text or f"{sop.title}\n{sop.description}"
            records.append({
                "number": sop.sop_id,
                "short_description": sop.title,
                "description": sop.description,
                "cmdb_ci_name": sop.service or None,
                "assignment_group": "",  # not applicable to SOPs
                "close_notes": "",
                "embedding_text": embedding_text,
                "source": "sop",
            })
        self.vector_store.upsert_incidents(records)
        logger.info("Indexed %d SOP(s) into the shared vector DB (source=sop).", len(records))

    def get(self, sop_id: str) -> Optional[SOP]:
        return self._sops.get(sop_id)

    def get_runbook(self, filename: str) -> Optional[dict]:
        # decision_tree steps reference paths like "runbooks/foo.json"
        return self._runbooks.get(os.path.basename(filename))

    def all(self) -> List[SOP]:
        return list(self._sops.values())

    def match(self, text: str) -> Optional[SOP]:
        """
        Simple, dependency-free keyword match: the SOP whose derived
        keywords (from sop_id/title/service/error_type) have the most
        hits in the incident text wins. Deterministic fallback that works
        regardless of whether vector indexing is enabled.
        """
        text_lower = (text or "").lower()
        best_sop, best_score = None, 0
        for sop in self._sops.values():
            score = sum(1 for kw in sop.keywords if kw in text_lower)
            if score > best_score:
                best_sop, best_score = sop, score
        return best_sop
