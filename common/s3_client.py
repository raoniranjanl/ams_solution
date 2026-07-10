"""
S3 client for the Job Remediation AI Agent.

Some jobs (e.g. the "Enrollments MMS job" - sop_documents/SOP-EAM-MMSO-701.json)
are diagnosed entirely from S3, not CloudWatch: an "error bucket" holding the
job's run log files, and a "keyword bucket" with fixed input/processed/error
folders holding the data files the job processes. This client provides:
  - Grounding-data retrieval for the Job Remediation Agent (recent file
    listings, latest log file content) - see agents/job_remediation_agent.py's
    _fetch_s3_context.
  - The one write operation a matching SOP's decision_tree can call for:
    moving keyword files back from error/ to input/ for retry - see
    common/remediation_executor.py's move_keyword_files_to_input action.

Fails soft throughout, same convention as common/cloudwatch_client.py: no
credentials, no permissions, or nothing found all just mean "no evidence
available" (empty list / None) - a normal, expected outcome the agent
already knows how to handle - not a crash.
"""
import logging
from typing import List, Optional

from common.aws_client import get_client
from config import S3Settings

logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self, settings: S3Settings):
        self.settings = settings

    def list_recent_objects(self, bucket: str, prefix: str = "", max_keys: Optional[int] = None) -> List[dict]:
        """
        Returns [{"key", "last_modified", "size"}, ...] for objects under
        `prefix`, most-recently-modified first. Skips zero-byte "folder
        marker" keys that exactly match the prefix. Empty list on any
        failure (bucket/prefix doesn't exist, no permissions, etc.) or if
        nothing is found - never raises.
        """
        max_keys = max_keys or self.settings.max_files
        try:
            s3 = get_client("s3")
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            contents = [c for c in resp.get("Contents", []) if c["Key"] != prefix]
            contents.sort(key=lambda c: c["LastModified"], reverse=True)
            contents = contents[:max_keys]
            return [
                {"key": c["Key"], "last_modified": c["LastModified"].isoformat(), "size": c["Size"]}
                for c in contents
            ]
        except Exception:
            logger.warning("Could not list objects in 's3://%s/%s'.", bucket, prefix, exc_info=True)
            return []

    def get_latest_object_text(self, bucket: str, prefix: str = "", max_bytes: Optional[int] = None) -> Optional[str]:
        """
        Fetches the decoded text content of the most recently modified
        object under `prefix` (e.g. a job's latest run log file). Returns
        None if nothing is found or the fetch fails for any reason.
        """
        recent = self.list_recent_objects(bucket, prefix=prefix, max_keys=1)
        if not recent:
            return None
        max_bytes = max_bytes or self.settings.max_log_bytes
        key = recent[0]["key"]
        try:
            s3 = get_client("s3")
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read(max_bytes)
            return body.decode("utf-8", errors="replace")
        except Exception:
            logger.warning("Could not read object '%s' from bucket '%s'.", key, bucket, exc_info=True)
            return None

    def move_objects(self, bucket: str, source_prefix: str, dest_prefix: str) -> List[str]:
        """
        Moves every object under source_prefix to the same relative path
        under dest_prefix (S3 has no native rename - copy then delete).
        Best-effort per object: a single object's copy/delete failure is
        logged and skipped rather than aborting the whole batch, and the
        source is only deleted after its copy succeeds - a partial
        failure duplicates a file rather than losing it. Returns the list
        of moved destination keys (empty list if none moved).
        """
        moved: List[str] = []
        s3 = get_client("s3")
        source_objects = self.list_recent_objects(bucket, prefix=source_prefix, max_keys=self.settings.max_files)

        for obj in source_objects:
            source_key = obj["key"]
            filename = source_key[len(source_prefix):]
            if not filename:
                continue
            dest_key = f"{dest_prefix.rstrip('/')}/{filename}"
            try:
                s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": source_key}, Key=dest_key)
                s3.delete_object(Bucket=bucket, Key=source_key)
                moved.append(dest_key)
            except Exception:
                logger.warning("Could not move 's3://%s/%s' to '%s'.", bucket, source_key, dest_key, exc_info=True)
        return moved
