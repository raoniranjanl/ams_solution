"""
EAM/Facets member-validation keyword-file job.

FULLY SOP-DRIVEN, INCLUDING SOP SELECTION: this job does not hardcode
which SOP it runs. On startup it loads EVERY SOP in the library
(common/sop_store.py, sop_documents/*.json) and picks the one whose
keyword_bucket matches the bucket being scanned (default: test-0v1-ioonh)
AND whose resolution_steps declare "export_eligible_member_hic" (the
action this job implements) - see find_sop_for_bucket() below. Today only
SOP-EAM-MEMVALID-801 matches, but dropping a kwd file into a DIFFERENT
bucket that another SOP declares (with a matching resolution_steps entry)
would make this job run against that SOP instead, with zero code changes.
Its error_prefix/input_prefix/output_filename also come from the matched
SOP's own fields, not hardcoded constants.

Reads kwd files from <sop.keyword_bucket>/<sop.error_prefix> (see
common/s3_client.py). Each kwd file holds one or more comma-separated
"@FIELD="VALUE"" records per line (real mainframe-style extract format,
e.g. RECTYPE="MEMD" detail lines mixed with RECTYPE="MEME" member lines).
Only the RECTYPE="MEME" lines carry @pMEME_HEALTH_ID - a single file can
therefore reference multiple members. For each one found, the member is
looked up in BOTH the eam table (by member_id) and the facets table (by
mem_health_id) - see common/eam_facets_store.py. A member is eligible only
when a record exists in BOTH tables and BOTH records independently pass
common/member_eligibility.py's check (has_active, planname MA/MAPD,
confirmation_letter_went, member_exist_in_eam, welcome_kit_triggered,
id_card_triggered, pcp_letter_went all true, and effective_date/term_date
currently valid).

- Any failed condition -> a console message naming what failed, member
  skipped.
- Eligible -> the member's HIC (from the facets record) is collected.

Each SOURCE kwd file gets its OWN output object in
<sop.keyword_bucket>/<sop.input_prefix>, named
"<sop.output_filename>_<YYYYMMDD_HHMMSS>.txt" (sop.output_filename acts as
a filename prefix, e.g. "EAF") - not one combined file for the whole run.
That file's content is a comma-separated, single-quoted line of the HICs
of every member from THAT source file who was eligible (e.g.
'1A2B3C4D5E6','9Z8Y7X6W5V4', or just '1A2B3C4D5E6' for a single eligible
member; an empty file if none were eligible). Source kwd files in error/
are not moved or deleted.

Requires AWS credentials (see .env / common/aws_client.py) with read/write
access to the bucket. Uses the same S3Client fail-soft conventions as the
rest of the Job Remediation Agent's S3 integration - a missing bucket or
permissions issue means "nothing found", not a crash.

Run: python -m jobs.process_eam_member_kwd_files [--bucket test-0v1-ioonh]
"""
import argparse
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from common.eam_facets_store import EamFacetsStore
from common.member_eligibility import check_record_eligibility
from common.s3_client import S3Client
from common.sop_store import SOP, SOPStore
from config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "test-0v1-ioonh"
DEFAULT_WATCH_INTERVAL_SECONDS = 30
REQUIRED_ACTION = "export_eligible_member_hic"  # the action this job implements

# Matches @pMEME_HEALTH_ID="<value>" anywhere in the file (quoted,
# real extract format). Falls back to an unquoted/':'-labeled form for
# robustness against slightly different kwd file conventions.
_FIELD_PATTERN = re.compile(r'@?pMEME_HEALTH_ID\s*=\s*"([^"]*)"', re.IGNORECASE)
_FIELD_PATTERN_UNQUOTED = re.compile(r"@?pMEME_HEALTH_ID\s*[:=]\s*([^,\s\"]+)", re.IGNORECASE)


def find_sop_for_bucket(sop_store: SOPStore, bucket: str) -> Tuple[Optional[SOP], Optional[str]]:
    """
    Scans EVERY loaded SOP (not a hardcoded id) for the one that owns
    `bucket`: its keyword_bucket must match, AND its resolution_steps must
    include REQUIRED_ACTION, since a bucket match alone isn't enough - a
    different S3-driven SOP (e.g. SOP-EAM-MMSO-701) could coincidentally
    share a bucket name without implementing this job's logic. Returns
    (sop, None) on a clean match, or (None, reason) if nothing usable was
    found.
    """
    candidates = [s for s in sop_store.all() if s.keyword_bucket == bucket]
    if not candidates:
        return None, f"No SOP in the library declares keyword_bucket='{bucket}'."

    matching = [s for s in candidates if REQUIRED_ACTION in s.resolution_steps]
    if not matching:
        names = ", ".join(s.sop_id for s in candidates)
        return None, (
            f"Found SOP(s) for bucket '{bucket}' ({names}) but none declare the "
            f"'{REQUIRED_ACTION}' action this job implements."
        )
    if len(matching) > 1:
        names = ", ".join(s.sop_id for s in matching)
        logger.warning(
            "Multiple SOPs match bucket '%s' and support '%s': %s - using the first one.",
            bucket, REQUIRED_ACTION, names,
        )
    return matching[0], None


def discover_kwd_sops(sop_store: SOPStore) -> List[SOP]:
    """
    Every SOP in the library that declares BOTH a keyword_bucket and the
    REQUIRED_ACTION this job implements - i.e. every bucket --watch should
    poll, with no bucket name hardcoded anywhere.
    """
    return [s for s in sop_store.all() if s.keyword_bucket and REQUIRED_ACTION in s.resolution_steps]


def parse_pmeme_health_ids(content: str) -> List[str]:
    """
    Extracts every pMEME_HEALTH_ID value present in a kwd file's content (a
    file can reference multiple members, one per RECTYPE="MEME" line).
    Order preserved, duplicates removed. Falls back to the whole (stripped)
    content as a single ID if no labeled field is found at all.
    """
    matches = _FIELD_PATTERN.findall(content) or _FIELD_PATTERN_UNQUOTED.findall(content)
    ids = [m.strip() for m in matches if m.strip()]
    if not ids:
        stripped = content.strip()
        return [stripped] if stripped else []

    seen = set()
    ordered_unique = []
    for member_id in ids:
        if member_id not in seen:
            seen.add(member_id)
            ordered_unique.append(member_id)
    return ordered_unique


def validate_member(store: EamFacetsStore, pmeme_health_id: str) -> Optional[str]:
    """
    Returns the member's HIC if eligible, else None (a console message
    explaining why is printed before returning).
    """
    eam_record = store.get_eam_by_member_id(pmeme_health_id)
    facets_record = store.get_facets_by_mem_health_id(pmeme_health_id)

    if not eam_record or not facets_record:
        missing = []
        if not eam_record:
            missing.append("no matching memberID in eam")
        if not facets_record:
            missing.append("no matching mem_health_id in facets")
        print(f"Some condition is not matching for pMEME_HEALTH_ID={pmeme_health_id}: {', '.join(missing)}.")
        return None

    eam_ok, eam_failed = check_record_eligibility(eam_record)
    facets_ok, facets_failed = check_record_eligibility(facets_record)

    if not eam_ok or not facets_ok:
        print(
            f"Some condition is not matching for pMEME_HEALTH_ID={pmeme_health_id}: "
            f"eam_failed_checks={eam_failed}, facets_failed_checks={facets_failed}."
        )
        return None

    return facets_record.get("hic")


def process_sop(sop: SOP) -> List[str]:
    """
    Processes every kwd file currently in the SOP's error/ prefix. Each
    SOURCE FILE gets its OWN output object in input/, named
    "<sop.output_filename>_<YYYYMMDD_HHMMSS>.txt" (sop.output_filename
    acts as a filename prefix here, e.g. "EAF") - one output per kwd file,
    not one combined file for the whole run. If two files in the same run
    would land on the same second, a "_2", "_3", ... suffix is appended so
    neither output gets silently overwritten.
    """
    settings = get_settings()
    s3_client = S3Client(settings.s3)
    store = EamFacetsStore(settings.postgres.url)

    bucket = sop.keyword_bucket
    error_prefix = sop.error_prefix
    input_prefix = sop.input_prefix
    filename_prefix = sop.output_filename

    files = s3_client.list_recent_objects(bucket, prefix=error_prefix)
    if not files:
        logger.warning("No kwd files found in s3://%s/%s - nothing to process.", bucket, error_prefix)

    all_eligible_hics: List[str] = []
    used_timestamps: Dict[str, int] = {}
    for obj in files:
        key = obj["key"]
        content = s3_client.get_object_text(bucket, key)
        if content is None:
            print(f"Some condition is not matching for file '{key}': could not read object content.")
            continue

        pmeme_health_ids = parse_pmeme_health_ids(content)
        if not pmeme_health_ids:
            print(f"Some condition is not matching for file '{key}': no pMEME_HEALTH_ID found in file content.")
            continue

        file_eligible_hics: List[str] = []
        for pmeme_health_id in pmeme_health_ids:
            hic = validate_member(store, pmeme_health_id)
            if hic:
                file_eligible_hics.append(hic)
        all_eligible_hics.extend(file_eligible_hics)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        used_timestamps[timestamp] = used_timestamps.get(timestamp, 0) + 1
        if used_timestamps[timestamp] > 1:
            timestamp = f"{timestamp}_{used_timestamps[timestamp]}"
        output_key = f"{input_prefix}{filename_prefix}_{timestamp}.txt"

        line = ",".join(f"'{hic}'" for hic in file_eligible_hics)
        written = s3_client.put_object_text(bucket, output_key, line)
        if written:
            logger.info(
                "Processed '%s': %d eligible member(s) -> wrote s3://%s/%s",
                key, len(file_eligible_hics), bucket, output_key,
            )
        else:
            logger.error("Failed to write result for '%s' to s3://%s/%s", key, bucket, output_key)

    return all_eligible_hics


def watch(interval_seconds: int) -> None:
    """
    Polls every kwd-owning SOP in the library on a timer, so a file
    dropped into any SOP-declared keyword_bucket's error/ prefix gets
    picked up automatically (within interval_seconds) without needing this
    command run by hand each time. Reloads the SOP library every cycle, so
    a newly added SOP (new bucket) starts getting watched without
    restarting this process. Each cycle re-validates against the live
    Postgres data too, so fixing a member's record there also takes effect
    on the very next cycle even if the kwd file itself hasn't changed.
    """
    settings = get_settings()
    logger.info("Watching for kwd files every %ss (Ctrl+C to stop)...", interval_seconds)
    while True:
        sop_store = SOPStore(settings.remediation.sop_dir)
        sops = discover_kwd_sops(sop_store)
        if not sops:
            logger.warning("No SOP in the library declares both a keyword_bucket and '%s'.", REQUIRED_ACTION)
        for sop in sops:
            logger.info(
                "Polling %s (%s): s3://%s/%s -> s3://%s/%s (per-file '%s_<timestamp>.txt' outputs)",
                sop.sop_id, sop.title, sop.keyword_bucket, sop.error_prefix,
                sop.keyword_bucket, sop.input_prefix, sop.output_filename,
            )
            process_sop(sop)
        time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(
        description="Process kwd files dropped into an S3 bucket, auto-selecting the SOP that owns it."
    )
    parser.add_argument(
        "--bucket", default=DEFAULT_BUCKET,
        help=f"(one-shot mode only) S3 bucket to scan - the whole SOP library is searched for the SOP "
             f"whose keyword_bucket matches this bucket (default: {DEFAULT_BUCKET}).",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Run continuously, polling every kwd-owning SOP in the library on a timer instead of "
             "running once against a single --bucket.",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_WATCH_INTERVAL_SECONDS,
        help=f"Seconds between polls in --watch mode (default: {DEFAULT_WATCH_INTERVAL_SECONDS}).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.watch:
        watch(args.interval)
        return

    settings = get_settings()
    sop_store = SOPStore(settings.remediation.sop_dir)

    sop, error = find_sop_for_bucket(sop_store, args.bucket)
    if sop is None:
        raise SystemExit(error)

    logger.info(
        "Bucket '%s' -> matched %s (%s). Reading s3://%s/%s, writing s3://%s/%s (per-file '%s_<timestamp>.txt' outputs)",
        args.bucket, sop.sop_id, sop.title, sop.keyword_bucket, sop.error_prefix,
        sop.keyword_bucket, sop.input_prefix, sop.output_filename,
    )
    process_sop(sop)


if __name__ == "__main__":
    main()
