"""
CloudWatch Logs client for the Job Remediation AI Agent.

Finds the relevant log group for a job/resource and pulls its most
recent error-level events, restricted to the last CLOUDWATCH_LOOKBACK_MINUTES
(default 30) since remediation decisions should only be based on very
recent activity, not stale history.

Log-group discovery tries, in order:
  1. An explicit SOP-provided log_group (exact name/prefix) - most reliable.
  2. Well-known AWS per-service naming conventions (e.g. "/aws/lambda/<name>",
     "/ecs/<name>", "/aws/rds/instance/<name>/error") built from the SOP's
     job_name and the ticket's CI name.
  3. AWS Glue's log groups are NOT per-job (Glue writes all jobs into a
     shared group - "/aws-glue/jobs/logs-v2" for jobs with continuous
     logging enabled, the modern default, or the legacy
     "/aws-glue/jobs/output"/"/aws-glue/jobs/error" pair otherwise), so
     for a GLUE-service SOP those are tried directly, with the job
     name added to the log filter so only that job's messages match.
  4. A full listing + substring match, but ONLY against short, specific
     identifier tokens (e.g. "agentic-dev-customer-etl-pipeline"), never
     a whole sentence - a prior version used the raw incident short
     description as the search hint, which almost never substring-matches
     a real log group name and was the main reason lookups kept failing.

Fails soft throughout: no credentials, no permissions, or no match all
just mean "no logs available" (empty list) - a normal, expected outcome
the agent already knows how to handle - not a crash.
"""
import logging
import time
from typing import List, Optional

from common.aws_client import get_client
from config import CloudWatchSettings

logger = logging.getLogger(__name__)


class CloudWatchLogsClient:
    def __init__(self, settings: CloudWatchSettings):
        self.settings = settings

    # ------------------------------------------------------------------
    # Log group discovery
    # ------------------------------------------------------------------
    def _log_group_exists(self, name: str) -> Optional[str]:
        """Exact/prefix existence check - cheap, avoids listing every group."""
        try:
            logs = get_client("logs")
            resp = logs.describe_log_groups(logGroupNamePrefix=name, limit=1)
            groups = resp.get("logGroups", [])
            if groups:
                return groups[0]["logGroupName"]
        except Exception:
            logger.debug("describe_log_groups prefix check failed for '%s'", name, exc_info=True)
        return None

    def _well_known_candidates(self, hint: str, service_hint: str = "") -> List[str]:
        service = (service_hint or "").upper()
        candidates = []
        if service == "LAMBDA" or not service:
            candidates.append(f"/aws/lambda/{hint}")
        if service in ("ECS", "") :
            candidates.append(f"/ecs/{hint}")
            candidates.append(f"/aws/ecs/{hint}")
        if service == "RDS" or not service:
            candidates.append(f"/aws/rds/instance/{hint}/error")
        return candidates

    def find_log_group(self, hint: str, service_hint: str = "", explicit_log_group: str = "") -> Optional[str]:
        """
        Resolves a log group using (in order): an explicit override, AWS's
        well-known per-service naming conventions, Glue's shared log
        groups (if service_hint indicates Glue), then a last-resort
        substring scan across all log groups using the (short!) hint.
        """
        # 1. Explicit SOP-provided override - most reliable when present.
        if explicit_log_group:
            found = self._log_group_exists(explicit_log_group)
            if found:
                logger.info("Using explicit SOP-provided log group '%s'", found)
                return found

        # 2. Well-known naming conventions built from the hint.
        if hint:
            for candidate in self._well_known_candidates(hint, service_hint):
                found = self._log_group_exists(candidate)
                if found:
                    logger.info("Matched log group '%s' via naming convention.", found)
                    return found

        # 3. Glue writes all jobs into shared log groups, not per-job ones.
        if service_hint.upper() == "GLUE":
            for candidate in ("/aws-glue/jobs/logs-v2", "/aws-glue/jobs/error", "/aws-glue/jobs/output"):
                found = self._log_group_exists(candidate)
                if found:
                    logger.info("Using Glue's shared log group '%s' (filtering by job name in content).", found)
                    return found

        # 4. Last resort: scan all log groups for a substring match. Only
        # worth doing with a short, specific token - a full sentence hint
        # will essentially never match a real log group name.
        if hint and len(hint) < 80 and " " not in hint.strip():
            try:
                logs = get_client("logs")
                hint_lower = hint.lower()
                paginator = logs.get_paginator("describe_log_groups")
                for page in paginator.paginate():
                    for group in page.get("logGroups", []):
                        name = group.get("logGroupName", "")
                        if hint_lower in name.lower():
                            logger.info("Matched log group '%s' via substring scan.", name)
                            return name
            except Exception:
                logger.warning("Could not scan CloudWatch log groups for hint '%s'.", hint, exc_info=True)

        return None

    # ------------------------------------------------------------------
    # Log event retrieval
    # ------------------------------------------------------------------
    def _resolve_glue_job_run_stream(self, job_name: str) -> Optional[str]:
        """
        Glue's log GROUPS are shared across all jobs - the actual
        per-run isolation is at the log STREAM level. This finds the most
        recent run of the given job via the Glue API and returns that run
        ID - far more precise than guessing at message content, since a
        run's error text often doesn't even mention the job name
        literally (e.g. a Python traceback just names the script file).
        Requires glue:GetJobRuns. Returns None (falls back to
        content-based filtering) if the job isn't found, has no runs yet,
        or the call fails for any reason.

        NOTE: the run ID itself is NOT necessarily the log stream name -
        see _find_log_stream, which resolves the actual stream.
        """
        if not job_name:
            return None
        try:
            glue = get_client("glue")
            resp = glue.get_job_runs(JobName=job_name, MaxResults=5)
            runs = resp.get("JobRuns", [])
            if not runs:
                return None
            latest = max(runs, key=lambda r: r.get("StartedOn", 0))
            run_id = latest.get("Id")
            logger.info("Resolved Glue job '%s' latest run to run ID '%s'", job_name, run_id)
            return run_id
        except Exception:
            logger.debug("Could not resolve Glue job runs for '%s'.", job_name, exc_info=True)
            return None

    def _find_log_stream(self, log_group_name: str, run_id: str) -> Optional[str]:
        """
        Resolves a Glue job run ID to its actual CloudWatch log stream
        name. In the legacy "/aws-glue/jobs/error"/"/aws-glue/jobs/output"
        groups the stream name IS the run ID, but in the modern
        "/aws-glue/jobs/logs-v2" continuous-logging group, AWS names
        streams "<run-id>-driver" (application/exception logs - where a
        SOP's "Failure Reason" text actually appears) and
        "<run-id>-executorNum" - see
        https://docs.aws.amazon.com/glue/latest/dg/monitor-continuous-logging.html.
        Requires logs:DescribeLogStreams. Returns None (falls back to
        content-based filtering) if no stream matches or the call fails.
        """
        try:
            logs = get_client("logs")
            resp = logs.describe_log_streams(logGroupName=log_group_name, logStreamNamePrefix=run_id)
            streams = [s["logStreamName"] for s in resp.get("logStreams", [])]
            if not streams:
                return None
            if run_id in streams:
                return run_id
            driver = next((s for s in streams if s.endswith("-driver")), None)
            if driver:
                return driver
            return sorted(streams)[0]
        except Exception:
            logger.debug("Could not resolve log stream for run '%s' in '%s'.", run_id, log_group_name, exc_info=True)
            return None

    def get_recent_error_events(
        self, log_group_name: str, extra_filter_text: str = "", limit: Optional[int] = None,
        log_stream_name: Optional[str] = None, skip_default_filter: bool = False,
    ) -> List[str]:
        """
        Pulls recent log events, within the last CLOUDWATCH_LOOKBACK_MINUTES
        (default 30 - remediation decisions should only look at very
        recent activity).

        - If log_stream_name is given (e.g. a resolved Glue job-run's
          actual log stream name, see _find_log_stream), this uses
          get_log_events(startFromHead=False) to pull the TAIL
          of that exact stream - i.e. the most recent N lines, which is
          where an exception/failure actually appears. This deliberately
          does NOT use filter_log_events here: that API's `limit` returns
          the *earliest* N matching events in the time window, which for
          a long-running job means startup/classpath/library-loading
          noise, cut off before ever reaching the real error at the end
          of the run - exactly the bug this fixes. No keyword filter is
          applied either, since we already know precisely which run this
          is; filtering by keyword would only risk dropping real content
          that doesn't happen to say "ERROR"/"Exception".
        - Otherwise (no specific stream - searching a log group by
          content), filter_log_events is used as before: skip_default_filter
          bypasses the generic keyword filter for groups that are already
          error-only by AWS's own design (e.g. the legacy "/aws-glue/jobs/error"),
          and extra_filter_text (if any) is AND'd into the filter pattern.
        """
        limit = limit or self.settings.max_log_events
        start_time_ms = int((time.time() - self.settings.lookback_minutes * 60) * 1000)

        if log_stream_name:
            return self._tail_log_stream(log_group_name, log_stream_name, start_time_ms, limit)

        try:
            logs = get_client("logs")
            filter_pattern = "" if skip_default_filter else self.settings.filter_pattern
            if extra_filter_text:
                filter_pattern = f'"{extra_filter_text}" {filter_pattern}'.strip()

            kwargs = {
                "logGroupName": log_group_name,
                "startTime": start_time_ms,
                "limit": limit,
                "interleaved": True,
            }
            if filter_pattern:
                kwargs["filterPattern"] = filter_pattern

            resp = logs.filter_log_events(**kwargs)
            events = resp.get("events", [])
            messages = [e.get("message", "").strip() for e in events if e.get("message")]
            logger.info(
                "Fetched %d CloudWatch log event(s) from '%s' (last %d min)",
                len(messages), log_group_name, self.settings.lookback_minutes,
            )
            return messages
        except Exception:
            logger.warning("Could not fetch CloudWatch log events from '%s'.", log_group_name, exc_info=True)
            return []

    def _tail_log_stream(self, log_group_name: str, log_stream_name: str, start_time_ms: int, limit: int) -> List[str]:
        """
        Returns the most recent `limit` events from a specific log stream,
        oldest-first within the returned set (so callers slicing [-N:] for
        "most recent N" get exactly that). Uses get_log_events with
        startFromHead=False, which AWS documents as returning the LATEST
        events first for pagination purposes while keeping the events
        within each response in chronological order - the correct API for
        "give me the tail of this stream", unlike filter_log_events.
        """
        try:
            logs = get_client("logs")
            resp = logs.get_log_events(
                logGroupName=log_group_name,
                logStreamName=log_stream_name,
                startTime=start_time_ms,
                limit=limit,
                startFromHead=False,
            )
            events = resp.get("events", [])
            messages = [e.get("message", "").strip() for e in events if e.get("message")]
            logger.info(
                "Tailed %d log event(s) from stream '%s' in '%s' (last %d min)",
                len(messages), log_stream_name, log_group_name, self.settings.lookback_minutes,
            )
            if not messages:
                # The run may have started before the lookback window (long
                # Glue jobs aren't unusual) - retry once without startTime
                # so we still get the tail of the stream regardless of age.
                logger.info(
                    "No events for stream '%s' within the last %d min - retrying without a time floor "
                    "in case the job run started earlier than that.",
                    log_stream_name, self.settings.lookback_minutes,
                )
                resp = logs.get_log_events(
                    logGroupName=log_group_name, logStreamName=log_stream_name,
                    limit=limit, startFromHead=False,
                )
                events = resp.get("events", [])
                messages = [e.get("message", "").strip() for e in events if e.get("message")]
                logger.info("Tailed %d log event(s) from stream '%s' (no time floor).", len(messages), log_stream_name)
            return messages
        except Exception:
            logger.warning("Could not tail log stream '%s' in '%s'.", log_stream_name, log_group_name, exc_info=True)
            return []

    def fetch_logs_for(self, hints: List[str], service_hint: str = "", explicit_log_group: str = "") -> List[str]:
        """
        Convenience wrapper: tries each hint (in priority order - typically
        [sop.job_name, ticket.cmdb_ci_name, extracted identifier tokens
        from the ticket text]) until a log group is found, then returns
        its recent events. Returns an empty list if nothing matched - a
        normal, expected outcome.
        """
        hints = [h for h in hints if h]
        log_group = None
        matched_hint = ""
        for hint in hints:
            log_group = self.find_log_group(hint, service_hint=service_hint, explicit_log_group=explicit_log_group)
            if log_group:
                matched_hint = hint
                break
        if not log_group and explicit_log_group:
            log_group = self._log_group_exists(explicit_log_group)

        if not log_group:
            logger.info("No CloudWatch log group found for any of hints=%s (service=%s).", hints, service_hint)
            return []

        is_glue_group = log_group.startswith("/aws-glue/")
        if is_glue_group and (service_hint.upper() == "GLUE" or matched_hint):
            # Best fix for Glue: resolve the exact run's log stream via the
            # Glue API rather than guessing from message content.
            run_id = self._resolve_glue_job_run_stream(matched_hint)
            stream = self._find_log_stream(log_group, run_id) if run_id else None
            if stream:
                return self.get_recent_error_events(log_group, log_stream_name=stream)
            # Couldn't resolve a run - fall back to content filtering, but
            # skip the generic keyword filter for the dedicated error group
            # since everything there is already an error by AWS's design.
            skip_default = log_group.endswith("/aws-glue/jobs/error")
            return self.get_recent_error_events(
                log_group, extra_filter_text=matched_hint, skip_default_filter=skip_default,
            )

        return self.get_recent_error_events(log_group)
