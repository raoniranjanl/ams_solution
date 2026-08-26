"""
EAM/Facets member-validation keyword-file job.

FULLY SOP-DRIVEN, INCLUDING SOP SELECTION: this job does not hardcode
which SOP it runs. On startup it loads EVERY SOP in the library
(common/sop_store.py, sop_documents/*.json) and picks the one whose
keyword_bucket matches the bucket being scanned (default: fet-to-mms-bucket)
AND whose resolution_steps declare "export_nonmatching_member_hic" (the
action this job implements) - see find_sop_for_bucket() below. Today only
SOP-EAM-MEMVALID-801 matches, but dropping a kwd file into a DIFFERENT
bucket that another SOP declares (with a matching resolution_steps entry)
would make this job run against that SOP instead, with zero code changes.
Its error_prefix/input_prefix/output_filename also come from the matched
SOP's own fields, not hardcoded constants.

READ and WRITE buckets can differ: kwd files are always READ from
<sop.keyword_bucket>/<sop.error_prefix>, but Legacy/output files are
WRITTEN to <sop.output_bucket>/<sop.input_prefix> - sop.output_bucket
falls back to sop.keyword_bucket (same bucket for both) when left unset,
so existing single-bucket SOPs are unaffected.

Reads kwd files from <sop.keyword_bucket>/<sop.error_prefix> (see
common/s3_client.py). Each kwd file holds one or more comma-separated
"@FIELD="VALUE"" records per line (real mainframe-style extract format,
e.g. RECTYPE="MEMD" detail lines mixed with RECTYPE="MEME" member lines).
Only the RECTYPE="MEME" lines carry @pMEME_HEALTH_ID - a single file can
therefore reference multiple members. For each one found, the member is
looked up in BOTH the eam table (by member_id) and the facets table (by
mem_health_id) - see common/eam_facets_store.py, and BOTH records are
checked independently via common/member_eligibility.py (has_active,
planname MA/MAPD, confirmation_letter_went, member_exist_in_eam,
welcome_kit_triggered, id_card_triggered, pcp_letter_went all true, and
effective_date/term_date currently valid).

THE EXPORT RULE IS INTENTIONALLY INVERTED from a typical "export the good
ones" pattern - this flags PROBLEM records for review, not clean ones:
- Not found in eam/facets, or ANY check fails on either record -> a
  console message naming what's wrong, AND that member's identifier is
  collected for export: facets.hic when a facets row exists, else
  eam.mbi as a fallback (data flows EAM -> Facets, so "in eam but not
  yet in facets" is the expected not-yet-propagated case, not an
  anomaly - mbi and hic are the same underlying value by convention).
  Only when NEITHER record exists is there nothing to export.
- ALL checks pass on BOTH records -> a console "All matching" message
  only. Nothing is exported for this member.

A member is only ever exported ONCE, ever - not once per run. Every
export is recorded in Postgres (common/member_export_log_store.py,
member_export_log table); a member already logged there is skipped (with
a console note) on every subsequent run, even if their kwd file is
processed again or they still fail the same checks.

Each SOURCE kwd file gets its OWN output object in
<sop.output_bucket or sop.keyword_bucket>/<sop.input_prefix>, named
"<sop.output_filename>_<YYYYMMDD_HHMMSS>.txt" (sop.output_filename acts as
a filename prefix, e.g. "Legacy") - not one combined file for the whole
run. That file's content is a comma-separated, single-quoted line of the
HICs of every member from THAT source file who did NOT fully match (e.g.
'1A2B3C4D5E6','9Z8Y7X6W5V4', or just '1A2B3C4D5E6' for a single flagged
member. If every member in that source file matched (or was already
exported previously), NO output file is written for it at all - no
0-byte objects.
Source kwd files in error/ are not moved or deleted.

SEPARATE MMS-FORWARDING FEATURE (only active when sop.mms_bucket is set):
any fully-matching member (across the WHOLE run, every source file
combined) whose facets row still has latest_changes_on_member_pcp=false
has their original kwd line forwarded verbatim into ONE new file,
s3://<mms_bucket>/<mms_input_prefix>EAF_<HHMMSS>.kwd - not written at all
if nothing qualifies. This is independent of the export/dedup logic above
and does NOT flip latest_changes_on_member_pcp itself, so the same member
is forwarded again on every future run until some other process updates
that column. See should_forward_to_mms().

Requires AWS credentials (see .env / common/aws_client.py) with read/write
access to the bucket. Uses the same S3Client fail-soft conventions as the
rest of the Job Remediation Agent's S3 integration - a missing bucket or
permissions issue means "nothing found", not a crash.

Run: python -m jobs.process_eam_member_kwd_files [--bucket fet-to-mms-bucket]
"""
import argparse
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from common.eam_facets_store import EamFacetsStore
from common.member_eligibility import check_record_eligibility
from common.member_export_log_store import MemberExportLogStore
from common.s3_client import S3Client
from common.sop_store import SOP, SOPStore
from config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "fet-to-mms-bucket"
DEFAULT_WATCH_INTERVAL_SECONDS = 30
REQUIRED_ACTION = "export_nonmatching_member_hic"  # the action this job implements

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


def parse_pmeme_records(content: str) -> List[Tuple[str, str]]:
    """
    Extracts (pMEME_HEALTH_ID, raw_line) pairs, one per RECTYPE="MEME" line
    that carries @pMEME_HEALTH_ID (a file can reference multiple members).
    The raw line is kept verbatim so it can be forwarded downstream
    unchanged (see should_forward_to_mms()). Order preserved, duplicate
    IDs removed (first occurrence wins). Falls back to a single
    (whole-content, whole-content) pair if no labeled field is found
    anywhere in the file.
    """
    records: List[Tuple[str, str]] = []
    seen = set()
    for line in content.splitlines():
        match = _FIELD_PATTERN.search(line) or _FIELD_PATTERN_UNQUOTED.search(line)
        if not match:
            continue
        member_id = match.group(1).strip()
        if not member_id or member_id in seen:
            continue
        seen.add(member_id)
        records.append((member_id, line.strip()))

    if not records:
        stripped = content.strip()
        if stripped:
            records.append((stripped, stripped))
    return records


def _export_once(
    export_log: MemberExportLogStore, pmeme_health_id: str, value: Optional[str],
    source_key: str, sop_id: str,
) -> Optional[str]:
    """
    Gates every export through the persistent member_export_log table: a
    member already recorded there (from ANY previous run) is never
    re-exported, even if they show up again in a later kwd file. Prints a
    console note when a repeat is skipped, for visibility.
    """
    if value is None:
        return None
    if export_log.has_been_exported(pmeme_health_id):
        print(f"pMEME_HEALTH_ID={pmeme_health_id} was already exported previously - skipping re-export.")
        return None
    export_log.record_export(pmeme_health_id, value, source_key=source_key, sop_id=sop_id)
    return value


def validate_member(
    export_log: MemberExportLogStore, eam_record: Optional[dict], facets_record: Optional[dict],
    pmeme_health_id: str, source_key: str, sop_id: str,
) -> Optional[str]:
    """
    INVERTED rule (see module docstring): returns an identifier to EXPORT
    when something does NOT match (missing record, or any eligibility/date
    check fails on either table), and prints an "All matching" console
    message with no export when everything passes. Every export is also
    gated through _export_once() so a member flagged once is never
    re-exported on a later run (see common/member_export_log_store.py).

    Data flows EAM -> Facets, so "in eam but not yet in facets" is the
    expected not-yet-propagated case, not an anomaly - mbi (eam) and hic
    (facets) are the same underlying identifier by convention, so that
    case still exports using eam.mbi as a fallback instead of being
    silently dropped for lack of a facets row. "in facets but not in eam"
    is atypical given the one-way flow, but if it happens facets.hic is
    exported the normal way. Only when NEITHER record exists is there no
    identifier at all to export.

    Takes already-fetched eam_record/facets_record (see process_sop()) so
    the caller can reuse the same lookup for should_forward_to_mms()
    without querying Postgres twice per member.
    """
    if not eam_record or not facets_record:
        missing = []
        if not eam_record:
            missing.append("no matching memberID in eam")
        if not facets_record:
            missing.append("no matching mem_health_id in facets")
        print(f"Some condition is not matching for pMEME_HEALTH_ID={pmeme_health_id}: {', '.join(missing)}.")
        value = facets_record.get("hic") if facets_record else (eam_record.get("mbi") if eam_record else None)
        return _export_once(export_log, pmeme_health_id, value, source_key, sop_id)

    eam_ok, eam_failed = check_record_eligibility(eam_record)
    facets_ok, facets_failed = check_record_eligibility(facets_record)

    if eam_ok and facets_ok:
        print(f"All matching for pMEME_HEALTH_ID={pmeme_health_id}.")
        return None

    print(
        f"Some condition is not matching for pMEME_HEALTH_ID={pmeme_health_id}: "
        f"eam_failed_checks={eam_failed}, facets_failed_checks={facets_failed}."
    )
    return _export_once(export_log, pmeme_health_id, facets_record.get("hic"), source_key, sop_id)


def should_forward_to_mms(eam_record: Optional[dict], facets_record: Optional[dict]) -> bool:
    """
    Separate rule, independent of the export/dedup logic above: a member
    who fully matches (same "All matching" condition as validate_member)
    but whose facets row still has latest_changes_on_member_pcp = false
    needs their original kwd record forwarded downstream to the MMS
    bucket for PCP processing. By design this does NOT flip that column -
    some other process is responsible for that, so the same member is
    forwarded again on every future run until it does.
    """
    if not eam_record or not facets_record:
        return False
    eam_ok, _ = check_record_eligibility(eam_record)
    facets_ok, _ = check_record_eligibility(facets_record)
    if not (eam_ok and facets_ok):
        return False
    return not facets_record.get("latest_changes_on_member_pcp", False)


def process_sop(sop: SOP) -> List[str]:
    """
    Processes every kwd file currently in the SOP's error/ prefix. Each
    SOURCE FILE gets its OWN output object in input/, named
    "<sop.output_filename>_<YYYYMMDD_HHMMSS>.txt" (sop.output_filename
    acts as a filename prefix here, e.g. "Legacy") - one output per kwd
    file, not one combined file for the whole run. If two files in the
    same run would land on the same second, a "_2", "_3", ... suffix is
    appended so neither output gets silently overwritten.

    Separately, if sop.mms_bucket is configured, every fully-matching
    member across the WHOLE run whose facets row has
    latest_changes_on_member_pcp = false has their original kwd record
    forwarded verbatim into ONE new file, s3://<mms_bucket>/<mms_input_prefix>EAF_<HHMMSS>.kwd
    (see should_forward_to_mms()). Not written at all if nothing qualifies.
    """
    settings = get_settings()
    s3_client = S3Client(settings.s3)
    store = EamFacetsStore(settings.postgres.url)
    export_log = MemberExportLogStore(settings.postgres.url)

    read_bucket = sop.keyword_bucket
    write_bucket = sop.output_bucket or sop.keyword_bucket
    error_prefix = sop.error_prefix
    input_prefix = sop.input_prefix
    filename_prefix = sop.output_filename

    files = s3_client.list_recent_objects(read_bucket, prefix=error_prefix)
    if not files:
        logger.warning("No kwd files found in s3://%s/%s - nothing to process.", read_bucket, error_prefix)

    all_flagged_hics: List[str] = []
    mms_forward_lines: List[str] = []
    used_timestamps: Dict[str, int] = {}
    for obj in files:
        key = obj["key"]
        content = s3_client.get_object_text(read_bucket, key)
        if content is None:
            print(f"Some condition is not matching for file '{key}': could not read object content.")
            continue

        pmeme_records = parse_pmeme_records(content)
        if not pmeme_records:
            print(f"Some condition is not matching for file '{key}': no pMEME_HEALTH_ID found in file content.")
            continue

        file_flagged_hics: List[str] = []
        for pmeme_health_id, raw_line in pmeme_records:
            eam_record = store.get_eam_by_member_id(pmeme_health_id)
            facets_record = store.get_facets_by_mem_health_id(pmeme_health_id)

            hic = validate_member(export_log, eam_record, facets_record, pmeme_health_id, key, sop.sop_id)
            if hic:
                file_flagged_hics.append(hic)

            if sop.mms_bucket and should_forward_to_mms(eam_record, facets_record):
                mms_forward_lines.append(raw_line)
        all_flagged_hics.extend(file_flagged_hics)

        if not file_flagged_hics:
            logger.info("Processed '%s': 0 non-matching member(s) - no output file written.", key)
            continue

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        used_timestamps[timestamp] = used_timestamps.get(timestamp, 0) + 1
        if used_timestamps[timestamp] > 1:
            timestamp = f"{timestamp}_{used_timestamps[timestamp]}"
        output_key = f"{input_prefix}{filename_prefix}_{timestamp}.txt"

        line = ",".join(f"'{hic}'" for hic in file_flagged_hics)
        written = s3_client.put_object_text(write_bucket, output_key, line)
        if written:
            logger.info(
                "Processed '%s': %d non-matching member(s) -> wrote s3://%s/%s",
                key, len(file_flagged_hics), write_bucket, output_key,
            )
        else:
            logger.error("Failed to write result for '%s' to s3://%s/%s", key, write_bucket, output_key)

    if sop.mms_bucket and mms_forward_lines:
        mms_filename = f"EAF_{datetime.now().strftime('%H%M%S')}.kwd"
        mms_key = f"{sop.mms_input_prefix}{mms_filename}"
        mms_content = "\n".join(mms_forward_lines) + "\n"
        mms_written = s3_client.put_object_text(sop.mms_bucket, mms_key, mms_content)
        if mms_written:
            logger.info(
                "Forwarded %d fully-matching member(s) with pending PCP changes -> wrote s3://%s/%s",
                len(mms_forward_lines), sop.mms_bucket, mms_key,
            )
        else:
            logger.error("Failed to write MMS forward file to s3://%s/%s", sop.mms_bucket, mms_key)

    return all_flagged_hics


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
                sop.output_bucket or sop.keyword_bucket, sop.input_prefix, sop.output_filename,
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
        sop.output_bucket or sop.keyword_bucket, sop.input_prefix, sop.output_filename,
    )
    process_sop(sop)


if __name__ == "__main__":
    main()
