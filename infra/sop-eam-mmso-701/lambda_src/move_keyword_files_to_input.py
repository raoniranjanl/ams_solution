"""
SOP-EAM-MMSO-701 - action: move_keyword_files_to_input.

Moves every pending file out of the keyword bucket's error/ folder back
into input/ so the Enrollments MMS job reprocesses it on its next
scheduled run. S3 has no native rename - copy then delete.
"""
import os

import boto3

s3 = boto3.client("s3")

KEYWORD_BUCKET = os.environ["KEYWORD_BUCKET"]


def handler(event, context):
    objects = [
        o for o in s3.list_objects_v2(Bucket=KEYWORD_BUCKET, Prefix="error/").get("Contents", [])
        if o["Key"] != "error/"
    ]

    moved = []
    for obj in objects:
        source_key = obj["Key"]
        filename = source_key[len("error/"):]
        dest_key = f"input/{filename}"
        s3.copy_object(Bucket=KEYWORD_BUCKET, CopySource={"Bucket": KEYWORD_BUCKET, "Key": source_key}, Key=dest_key)
        s3.delete_object(Bucket=KEYWORD_BUCKET, Key=source_key)
        moved.append(dest_key)

    return {"moved_count": len(moved), "moved_keys": moved}
