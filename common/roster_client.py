"""
Weekly on-call roster lookup.

Reads a "Weekly Roster" spreadsheet (Domain, Date, Shift, Start, End,
Member) and answers: "who was on shift for <domain> at <timestamp>?"

The spreadsheet is expected to have one row per (domain, date, shift),
e.g.:

Domain                    Date        Shift     Start   End     Member
Enrollment L2 OFFSHORE   2026-07-21  Shift A   07:00   15:00   Bharat Cheparthi
Enrollment L2 OFFSHORE   2026-07-21  Shift B   15:00   23:00   Naik Kavitha Ganesh

This is used to resolve the ServiceNow "Assigned to" field: when an
incident lands in an assignment group that maps to a roster domain, we
look up whoever was on shift at the incident's created-on time and use
that as the assignee.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Dict, List, Optional

import openpyxl

logger = logging.getLogger(__name__)


@dataclass
class ShiftEntry:
    domain: str
    shift_date: date
    shift_name: str
    start: time
    end: time
    member: str

    def covers(self, at: datetime) -> bool:
        if at.date() != self.shift_date:
            return False
        t = at.time()
        if self.start <= self.end:
            return self.start <= t < self.end
        # Overnight shift (e.g. 23:00 -> 07:00) - not used today but handled defensively.
        return t >= self.start or t < self.end


def _parse_time(value) -> time:
    """Accepts a datetime.time, a datetime.datetime, or a 'HH:MM' string."""
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, str):
        hh, mm = value.strip().split(":")[:2]
        return time(int(hh), int(mm))
    raise ValueError(f"Unrecognized time value in roster: {value!r}")


def _parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    raise ValueError(f"Unrecognized date value in roster: {value!r}")


class RosterClient:
    """Loads a Weekly Roster .xlsx file and resolves shift assignees."""

    def __init__(self, file_path: str, sheet_name: Optional[str] = None):
        self.file_path = file_path
        self.sheet_name = sheet_name
        self._entries: List[ShiftEntry] = []
        self.reload()

    def reload(self) -> None:
        """(Re)read the roster file from disk. Call this periodically/on a
        schedule if the roster spreadsheet can change during the week."""
        wb = openpyxl.load_workbook(self.file_path, data_only=True)
        ws = wb[self.sheet_name] if self.sheet_name else wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            self._entries = []
            return

        header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
        try:
            idx = {
                "domain": header.index("domain"),
                "date": header.index("date"),
                "shift": header.index("shift"),
                "start": header.index("start"),
                "end": header.index("end"),
                "member": header.index("member"),
            }
        except ValueError as e:
            raise ValueError(
                f"Roster file {self.file_path} is missing an expected column "
                f"(need Domain, Date, Shift, Start, End, Member): {e}"
            )

        entries: List[ShiftEntry] = []
        for row in rows[1:]:
            if row is None or all(v is None for v in row):
                continue
            try:
                entries.append(ShiftEntry(
                    domain=str(row[idx["domain"]]).strip(),
                    shift_date=_parse_date(row[idx["date"]]),
                    shift_name=str(row[idx["shift"]]).strip(),
                    start=_parse_time(row[idx["start"]]),
                    end=_parse_time(row[idx["end"]]),
                    member=str(row[idx["member"]]).strip(),
                ))
            except Exception:
                logger.exception("Skipping unparsable roster row: %r", row)

        self._entries = entries
        logger.info("Loaded %d roster entries from %s", len(entries), self.file_path)

    def get_assignee(self, domain: str, at: datetime) -> Optional[str]:
        """Return the roster Member scheduled for `domain` at timestamp `at`,
        or None if nobody is on shift at that time."""
        domain_norm = domain.strip().lower()
        for entry in self._entries:
            if entry.domain.strip().lower() == domain_norm and entry.covers(at):
                return entry.member
        logger.warning("No roster match for domain=%s at=%s", domain, at.isoformat())
        return None


class DomainResolver:
    """Maps a ServiceNow assignment group name to the roster 'Domain' it
    corresponds to, so the orchestrator doesn't need to hard-code the
    mapping inline."""

    def __init__(self, group_to_domain: Dict[str, str]):
        # normalize keys for case-insensitive lookup
        self._map = {k.strip().lower(): v for k, v in group_to_domain.items()}

    def resolve(self, assignment_group: str) -> Optional[str]:
        return self._map.get(assignment_group.strip().lower())
