"""
SOP-EAM-MMSO-701 - STEP 2: Verify remediation outcome.

Re-reads the error bucket's latest run log after the retry wait. This
job is diagnosed entirely from S3, so "SUCCEEDED" is inferred from the
absence of a new "Failed at step" entry in the newest log file.
"""
import os

import boto3

s3 = boto3.client("s3")

ERROR_BUCKET = os.environ["ERROR_BUCKET"]


def _latest_key(bucket, prefix=""):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = [o for o in resp.get("Contents", []) if o["Key"] != prefix]
    if not contents:
        return None
    return max(contents, key=lambda o: o["LastModified"])["Key"]


def handler(event, context):
    log_key = _latest_key(ERROR_BUCKET)
    if not log_key:
        return {"job_run_state": "UNKNOWN"}

    body = s3.get_object(Bucket=ERROR_BUCKET, Key=log_key)["Body"].read().decode("utf-8", "replace")
    job_run_state = "FAILED" if "ERROR: Failed" in body else "SUCCEEDED"
    return {"log_key": log_key, "job_run_state": job_run_state}
