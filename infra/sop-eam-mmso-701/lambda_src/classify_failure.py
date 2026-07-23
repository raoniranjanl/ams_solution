"""
SOP-EAM-MMSO-701 - STEP 1: Classify the Enrollments MMS job failure.

Reads the most recent log file in the error bucket, extracts the
"Failed at step <N>" number, and checks whether the keyword bucket's
error/ folder currently holds any pending files. Returns both facts so
the state machine's Choice state can apply the SOP's decision table.
"""
import os
import re

import boto3

s3 = boto3.client("s3")

ERROR_BUCKET = os.environ["ERROR_BUCKET"]
KEYWORD_BUCKET = os.environ["KEYWORD_BUCKET"]

STEP_PATTERN = re.compile(r"Failed at step (\d+)", re.IGNORECASE)


def _latest_key(bucket, prefix=""):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = [o for o in resp.get("Contents", []) if o["Key"] != prefix]
    if not contents:
        return None
    return max(contents, key=lambda o: o["LastModified"])["Key"]


def handler(event, context):
    log_key = _latest_key(ERROR_BUCKET)
    if not log_key:
        raise RuntimeError(f"No run log files found in s3://{ERROR_BUCKET}")

    body = s3.get_object(Bucket=ERROR_BUCKET, Key=log_key)["Body"].read().decode("utf-8", "replace")
    match = STEP_PATTERN.search(body)
    if not match:
        raise RuntimeError(f"Could not find 'Failed at step <N>' in s3://{ERROR_BUCKET}/{log_key}")
    failed_step = int(match.group(1))

    error_files = [
        o for o in s3.list_objects_v2(Bucket=KEYWORD_BUCKET, Prefix="error/").get("Contents", [])
        if o["Key"] != "error/"
    ]

    return {
        "log_key": log_key,
        "failed_step": failed_step,
        "keyword_error_files_present": len(error_files) > 0,
        "keyword_error_file_count": len(error_files),
    }
