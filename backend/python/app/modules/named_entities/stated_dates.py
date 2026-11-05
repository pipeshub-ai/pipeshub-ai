"""The day a record's own text says it was written or last changed.

For a record whose stored dates are only its upload's: an exported ticket,
chat thread or email keeps its dates in its text, not in the file. The first
rule that finds a day wins:

- message headers (an email thread's ``Date:`` or ``Sent:`` lines): the latest;
- log or comment entries (lines that open with a day, ``2025-05-07 15:45 UTC - Felix: ...``): the latest;
- labelled dates anywhere (``Last updated:``, ``Created:``, never ``Due:`` or
  ``Effective:``, as ``document_dates.label_kind`` reads them): the latest
  change, else the creation;
- a Unix time standing alone in the file name (chat exports name a thread by
  its first message's time): that day;
- otherwise the latest full date the text mentions, which is only a hint.

``document_dates`` reads the same labels from the date entities extracted from
a document's header; this reads the whole text with patterns, so it needs no
extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from app.modules.named_entities.document_dates import label_kind

Basis = Literal["message header", "log entry", "labelled date", "file name", "latest date mentioned"]

_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1,
)}
_MONTH = r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_PATTERNS = (
    # (?!\d), not \b: an ISO timestamp's T follows the day directly.
    re.compile(r"\b(?P<y>(?:19|20)\d\d)-(?P<m>\d\d)-(?P<d>\d\d)(?!\d)"),
    re.compile(_MONTH + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>(?:19|20)\d\d)\b", re.IGNORECASE),
    re.compile(r"\b(?P<d>\d{1,2})\s+" + _MONTH + r",?\s+(?P<y>(?:19|20)\d\d)\b", re.IGNORECASE),
)
_MESSAGE_HEADER = re.compile(r"^\s*(?:[>*_]\s*)*(?:date|sent)\s*:\s*(?P<rest>.+)$", re.IGNORECASE | re.MULTILINE)
# A line (or table row) that opens with a day, an optional time and zone, then a separator.
_LOG_ENTRY = re.compile(
    r"^\s*(?:[-*•>|]\s*)*(?:\*\*)?(?P<y>(?:19|20)\d\d)-(?P<m>\d\d)-(?P<d>\d\d)"
    r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\s*[A-Za-z]{1,5})?)?(?:\*\*)?\s*(?:[-–—|:]|\])",
    re.MULTILINE,
)
_FENCE = re.compile(r"```.*?```", re.DOTALL)
# 2001-09-09 to 2033-05-18; bounded by non-alphanumerics so a run of digits inside an id never counts.
_UNIX_NAME = re.compile(r"(?<![0-9A-Za-z])(1\d{9})(?:\.\d+)?(?![0-9A-Za-z])")
# A label opens its line within this many characters of the date. label_kind reads from the line's start, so on
# one long unbroken line every date would rescan it.
_LABEL_REACH = 120


@dataclass(frozen=True)
class StatedDate:
    day: date
    basis: Basis


def _day(year: str, month: str | int, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _days_with_spans(text: str) -> list[tuple[int, date]]:
    """Every full date in ``text`` with where it starts."""
    found: list[tuple[int, date]] = []
    for pattern in _DAY_PATTERNS:
        for match in pattern.finditer(text):
            parts = match.groupdict()
            month = parts.get("m") or _MONTHS[parts["mon"].lower()[:3]]
            day = _day(parts["y"], month, parts["d"])
            if day is not None:
                found.append((match.start(), day))
    return found


def _message_header_days(text: str) -> list[date]:
    days: list[date] = []
    for match in _MESSAGE_HEADER.finditer(text):
        days += [day for _start, day in _days_with_spans(match["rest"])]
    return days


def _log_entry_days(text: str) -> list[date]:
    return [day for match in _LOG_ENTRY.finditer(text) if (day := _day(match["y"], match["m"], match["d"]))]


def _labelled_days(text: str) -> tuple[list[date], list[date]]:
    """(changed, created) days on labelled lines."""
    changed: list[date] = []
    created: list[date] = []
    for start, day in _days_with_spans(text):
        head = text[max(0, start - _LABEL_REACH):start]
        if start > _LABEL_REACH and "\n" not in head and "\\n" not in head:
            continue
        kind = label_kind(head, len(head))
        if kind == "modified":
            changed.append(day)
        elif kind == "created":
            created.append(day)
    return changed, created


def _file_name_day(name: str) -> date | None:
    match = _UNIX_NAME.search(name)
    if match is None:
        return None
    return datetime.fromtimestamp(int(match.group(1)), UTC).date()


def stated_date(name: str, text: str) -> StatedDate | None:
    """The day the record states for itself, and how it was read; None when it states none."""
    unquoted = _FENCE.sub(" ", text)
    if days := _message_header_days(unquoted):
        return StatedDate(max(days), "message header")
    if days := _log_entry_days(unquoted):
        return StatedDate(max(days), "log entry")
    changed, created = _labelled_days(unquoted)
    if changed or created:
        return StatedDate(max(changed or created), "labelled date")
    if (day := _file_name_day(name)) is not None:
        return StatedDate(day, "file name")
    if mentioned := [day for _start, day in _days_with_spans(unquoted)]:
        return StatedDate(max(mentioned), "latest date mentioned")
    return None


__all__ = ["Basis", "StatedDate", "stated_date"]
