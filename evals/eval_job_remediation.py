"""
Live-LLM evaluation harness for the Job Remediation AI Agent.

Unlike tests/test_job_remediation_agent.py (fully mocked, no API calls,
tests the deterministic code scaffolding), this script calls the REAL
Anthropic API against a small set of representative scenarios and scores
the model's actual action/confidence/requires_human_approval choices -
this is the tool for measuring whether prompt changes in
agents/job_remediation_agent.py actually improved things.

It uses the real SOPStore (loaded from sop_documents/) so scenarios are
graded against the actual SOP library, but stubs out the vector store
(returns no similar-incident history) and CloudWatch (returns canned log
lines per scenario) to isolate prompt quality from embedding/Qdrant/AWS
infrastructure.

Requires ANTHROPIC_API_KEY. Costs a small number of real API calls.
Results can vary run-to-run since this exercises a real LLM (temperature
is fixed low by the agent itself, but not literally 0) - this is a manual/
ad-hoc tuning tool, not part of CI or the mocked test suite.

Usage:
    python -m evals.eval_job_remediation
"""
import logging
from typing import List, Optional

from agents.job_remediation_agent import JobRemediationAgent
from common.llm_client import AnthropicAgentClient
from common.models import Ticket
from common.sop_store import SOPStore
from config import get_settings

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class _NullVectorStore:
    """Stub: no similar-incident history, isolates prompt quality from Qdrant/embeddings."""

    def search(self, query_text: str, top_k: int = 5, source_filter: Optional[str] = None):
        return []


class _CannedCloudWatchClient:
    """Stub: returns a fixed list of log lines regardless of hints, per scenario."""

    def __init__(self, log_events: List[str]):
        self._log_events = log_events

    def fetch_logs_for(self, hints, service_hint: str = "", explicit_log_group: str = "") -> List[str]:
        return self._log_events


# Each scenario: ticket text + canned logs + expected outcome. Expected
# fields set to None are not checked (the model has legitimate room to
# vary there) - only non-None expectations count toward pass/fail.
SCENARIOS = [
    {
        "name": "glue_connection_refused_matches",
        "short_description": "agentic-dev-customer-etl-pipeline Glue job failed",
        "description": "The nightly Customer ETL Glue job run failed overnight.",
        "cmdb_ci_name": "agentic-dev-customer-etl-pipeline",
        "log_events": [
            '{"Event": "GlueETLJobExceptionEvent", "Failure Reason": "RuntimeError: connection refused: '
            "required upstream table 'customer_raw' is currently locked by another job.\"}",
        ],
        "expected_action": "retry_glue_job",
        "expected_requires_human_approval": False,
        "expected_insufficient_information": False,
        "min_confidence": 0.7,
    },
    {
        "name": "glue_non_matching_failure_routes_to_human",
        "short_description": "agentic-dev-customer-etl-pipeline Glue job failed",
        "description": "The nightly Customer ETL Glue job run failed overnight.",
        "cmdb_ci_name": "agentic-dev-customer-etl-pipeline",
        "log_events": [
            '{"Event": "GlueETLJobExceptionEvent", "Failure Reason": "java.lang.OutOfMemoryError: Java heap space"}',
        ],
        "expected_action": "manual_review",
        "expected_requires_human_approval": True,
        "expected_insufficient_information": False,
    },
    {
        "name": "glue_sop_only_no_logs_recommends_for_human_approval",
        "short_description": "agentic-dev-customer-etl-pipeline Glue job failed",
        "description": "The nightly Customer ETL Glue job run failed overnight.",
        "cmdb_ci_name": "agentic-dev-customer-etl-pipeline",
        "log_events": [],
        # SOP matched, no logs -> must still recommend the SOP's action
        # (not insufficient_information) and require human approval.
        "expected_action": "retry_glue_job",
        "expected_requires_human_approval": True,
        "expected_insufficient_information": False,
        "min_confidence": 0.4,
        "max_confidence": 0.9,
    },
    {
        "name": "no_sop_logs_only_registry_action",
        # Deliberately avoids every SOP's keyword substrings (see
        # SOPStore.match()) so this scenario reliably matches NO SOP -
        # naive substring keyword scoring means even words like "and",
        # "not", "out", "because" collide with unrelated SOP keywords.
        "short_description": "public-website-frontend keeps restarting due to upstream connectivity issues",
        "description": "The public-website-frontend service keeps restarting, as it is unable to reach its "
                       "upstream dependency, seeing repeated restarts overnight.",
        "cmdb_ci_name": "public-website-frontend",
        "log_events": [
            "ERROR connection timed out to upstream database, health check failing",
            "ERROR connection timed out to upstream database, health check failing",
        ],
        "expected_action": None,  # best-effort - depends on whether params are extractable
        "expected_requires_human_approval": None,
        "expected_insufficient_information": None,
    },
    {
        "name": "neither_source_short_circuits",
        "short_description": "Question about requesting VPN access",
        "description": "User is asking how to request VPN access for a new laptop.",
        "cmdb_ci_name": None,
        "log_events": [],
        "expected_action": None,
        "expected_requires_human_approval": True,
        "expected_insufficient_information": True,
    },
]


def _run_scenario(agent: JobRemediationAgent, scenario: dict, cloudwatch_client_holder: dict):
    cloudwatch_client_holder["client"] = _CannedCloudWatchClient(scenario["log_events"])
    ticket = Ticket(
        sys_id=f"eval-{scenario['name']}", number=f"EVAL-{scenario['name']}", table="incident",
        sys_class_name="incident", short_description=scenario["short_description"],
        description=scenario["description"], cmdb_ci_name=scenario.get("cmdb_ci_name"),
    )
    return agent.get_remediation(ticket)


def _check(actual, expected, label: str, checks: list):
    if expected is None:
        return
    ok = actual == expected
    checks.append((label, expected, actual, ok))


def main():
    settings = get_settings()
    if not settings.anthropic.api_key:
        print("ANTHROPIC_API_KEY is not set - cannot run a live-LLM eval. Set it in your .env first.")
        return

    sop_store = SOPStore(settings.remediation.sop_dir, vector_store=None)
    llm_client = AnthropicAgentClient(settings.anthropic)
    vector_store = _NullVectorStore()

    cloudwatch_holder = {"client": None}

    class _ForwardingCloudWatchClient:
        def fetch_logs_for(self, hints, service_hint="", explicit_log_group=""):
            return cloudwatch_holder["client"].fetch_logs_for(hints, service_hint, explicit_log_group)

    agent = JobRemediationAgent(
        vector_store=vector_store, llm_client=llm_client, anthropic_settings=settings.anthropic,
        sop_store=sop_store, cloudwatch_client=_ForwardingCloudWatchClient(),
    )

    results = []
    for scenario in SCENARIOS:
        recommendation = _run_scenario(agent, scenario, cloudwatch_holder)

        checks = []
        _check(recommendation.action, scenario.get("expected_action"), "action", checks)
        _check(recommendation.requires_human_approval, scenario.get("expected_requires_human_approval"),
               "requires_human_approval", checks)
        _check(recommendation.insufficient_information, scenario.get("expected_insufficient_information"),
               "insufficient_information", checks)

        min_conf = scenario.get("min_confidence")
        if min_conf is not None:
            checks.append(("confidence>=min", f">={min_conf}", recommendation.confidence,
                            recommendation.confidence >= min_conf))
        max_conf = scenario.get("max_confidence")
        if max_conf is not None:
            checks.append(("confidence<=max", f"<={max_conf}", recommendation.confidence,
                            recommendation.confidence <= max_conf))

        passed = all(ok for _, _, _, ok in checks) if checks else True
        results.append((scenario["name"], recommendation, checks, passed))

    print("\n" + "=" * 100)
    print("Job Remediation Agent live-LLM eval results")
    print("=" * 100)
    for name, rec, checks, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"\n[{status}] {name}")
        print(f"  action={rec.action!r} confidence={rec.confidence:.2f} "
              f"requires_human_approval={rec.requires_human_approval} "
              f"insufficient_information={rec.insufficient_information}")
        print(f"  rationale: {rec.rationale[:200]}")
        for label, expected, actual, ok in checks:
            mark = "ok" if ok else "MISMATCH"
            print(f"    - {label}: expected={expected!r} actual={actual!r} [{mark}]")

    total = len(results)
    passed_count = sum(1 for *_rest, passed in results if passed)
    avg_confidence = sum(r.confidence for _, r, _, _ in results) / total if total else 0.0
    print("\n" + "-" * 100)
    print(f"Summary: {passed_count}/{total} scenarios passed all checked expectations. "
          f"Average confidence across all scenarios: {avg_confidence:.2f}")
    print("-" * 100)


if __name__ == "__main__":
    main()
