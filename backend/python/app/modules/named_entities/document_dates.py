"""The dates a document states for itself, read from its extracted date entities.

Only a labelled line near the top counts: an email's Date: or Sent:, a memo's
or meeting header's Date:, front matter's Created: and Last updated:. A date the
text only mentions (a deadline, the day of an incident) never does, and a
header that gives one thing two different days is ambiguous. Further down, only
an email thread's later message headers count, as when it last changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.values import DateRangeValue
from app.modules.named_entities.text import is_prose_text, text_units_from_blocks

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.modules.named_entities.domain.models import (
        NamedEntity,
        NamedEntityExtraction,
    )

_LabelKind = Literal["created", "modified"]

# How much of a document's opening a header may take up.
HEADER_CHARS = 1500
HEADER_BLOCKS = 8
# A document is not dated after its upload; the slack covers time zones.
_AFTER_UPLOAD_SLACK_MS = 86_400_000
# Day zero and negative epochs are no document dates.
_EARLIEST_MS = 86_400_000

_DATE_KINDS = frozenset({EntityKind.DATE, EntityKind.DATE_TIME})
_DAY_GRANULARITIES = frozenset({"day", "hour", "minute"})
_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
# The text between a line's start and the date: an optional bullet, a short
# label, a colon and an optional weekday ("Sent: Tuesday, March 3, 2025").
_LABEL_PREFIX = re.compile(
    r"^\s*(?:[-*•>]\s*)*(?:\*\*|__)?(?P<label>[A-Za-z][A-Za-z /&'.-]{0,40}?)\s*(?:\*\*|__)?\s*:"
    r"\s*(?:\*\*|__)?\s*(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?$",
    re.IGNORECASE,
)
_MESSAGE_HEADER = re.compile(r"^\s*(?:\*\*)?from\s*:", re.IGNORECASE | re.MULTILINE)
_MAX_LABEL_WORDS = 4
_CREATED_WORDS = frozenset({
    "date", "dated", "sent", "created", "published", "posted", "issued", "written",
    "recorded", "meeting", "submitted", "filed", "held",
})
_MODIFIED_WORDS = frozenset({"updated", "modified", "revised", "edited", "revision"})
# A label for another event's day: "Due date:", "Effective date:", "Release date:".
_OTHER_EVENT_WORDS = frozenset({
    "due", "deadline", "effective", "expiry", "expires", "expiration", "start", "starts",
    "end", "ends", "launch", "release", "target", "eta", "renewal", "next", "incident",
    "outage", "event", "kickoff", "review", "valid", "until", "close", "closing",
    "delivery", "ship", "birth", "travel", "arrival", "departure", "live", "cutover",
})


@dataclass(frozen=True)
class DocumentDates:
    """Midnight UTC of the days a document states it was written and last changed."""

    created_ms: int
    modified_ms: int


@dataclass(frozen=True)
class _Labelled:
    position: int
    block_index: int
    kind: _LabelKind
    day_ms: int
    in_header: bool
    in_message: bool


@dataclass(frozen=True)
class _Unit:
    offset: int
    text: str
    in_header: bool
    is_message: bool


def find_document_dates(
    extraction: NamedEntityExtraction,
    blocks: Sequence[object],
    *,
    uploaded_ms: int | None,
) -> DocumentDates | None:
    """The dates the document's header states, or None when it states none or
    is ambiguous. ``uploaded_ms`` bounds them: a header dated after the upload
    is not believed."""
    units = _text_units(blocks)
    found = sorted(
        (labelled for entity in extraction.entities if entity.kind in _DATE_KINDS
         for labelled in _labelled_mentions(entity, units)),
        key=lambda labelled: labelled.position,
    )
    header = [labelled for labelled in found if labelled.in_header]
    if not header:
        return None
    latest = uploaded_ms + _AFTER_UPLOAD_SLACK_MS if uploaded_ms else None
    if latest is not None and any(labelled.day_ms > latest for labelled in header):
        return None
    created = [labelled for labelled in header if labelled.kind == "created"]
    if created and _ambiguous(created):
        return None
    modified_days = [labelled.day_ms for labelled in header if labelled.kind == "modified"]
    created_ms = created[0].day_ms if created else min(modified_days)
    thread_days = [
        labelled.day_ms for labelled in found
        if labelled.in_message and labelled.kind == "created" and (latest is None or labelled.day_ms <= latest)
    ]
    modified_ms = max([created_ms, *modified_days, *thread_days])
    return DocumentDates(created_ms=created_ms, modified_ms=modified_ms)


def _text_units(blocks: Sequence[object]) -> dict[int, _Unit]:
    units: dict[int, _Unit] = {}
    offset = 0
    for position, unit in enumerate(text_units_from_blocks(blocks, keep=is_prose_text)):
        in_header = position < HEADER_BLOCKS and offset < HEADER_CHARS
        units[unit.block_index] = _Unit(offset, unit.text, in_header, bool(_MESSAGE_HEADER.search(unit.text)))
        offset += len(unit.text) + 1
    return units


def _labelled_mentions(entity: NamedEntity, units: dict[int, _Unit]) -> list[_Labelled]:
    day_ms = _stated_day(entity.value)
    if day_ms is None:
        return []
    labelled = []
    for mention in entity.mentions:
        unit = units.get(mention.block_index)
        if unit is None or unit.text[mention.char_start:mention.char_end] != mention.surface:
            continue
        if unit.text.count("```", 0, mention.char_start) % 2:
            # Quoted output, such as an HTTP response's own Date: header.
            continue
        position = unit.offset + mention.char_start
        in_header = unit.in_header and position < HEADER_CHARS
        if not (in_header or unit.is_message):
            continue
        kind = _label_kind(unit.text, mention.char_start)
        if kind is not None:
            labelled.append(_Labelled(position, mention.block_index, kind, day_ms, in_header, unit.is_message))
    return labelled


def _stated_day(value: object) -> int | None:
    """Midnight UTC of the calendar day an explicit, fully dated value names."""
    if (
        not isinstance(value, DateRangeValue)
        or value.ambiguous
        or value.start_ms is None
        or value.anchor_source != "explicit"
        or value.granularity not in _DAY_GRANULARITIES
    ):
        return None
    match = _ISO_DAY.match(value.timex)
    if match is None:
        return None
    try:
        day = datetime(int(match[1]), int(match[2]), int(match[3]), tzinfo=UTC)
    except ValueError:
        return None
    day_ms = int(day.timestamp() * 1000)
    return day_ms if day_ms >= _EARLIEST_MS else None


def _label_kind(text: str, start: int) -> _LabelKind | None:
    """What the label opening the line up to ``start`` dates, if it dates the document."""
    line_start = text.rfind("\n", 0, start) + 1
    # Some exports leave line breaks as a literal backslash-n.
    escaped = text.rfind("\\n", line_start, start)
    if escaped >= 0:
        line_start = escaped + 2
    match = _LABEL_PREFIX.match(text[line_start:start])
    if match is None:
        return None
    words = re.findall(r"[a-z]+", match["label"].lower())
    if len(words) > _MAX_LABEL_WORDS or not words or _OTHER_EVENT_WORDS.intersection(words):
        return None
    if _MODIFIED_WORDS.intersection(words):
        return "modified"
    if _CREATED_WORDS.intersection(words):
        return "created"
    return None


def _ambiguous(created: list[_Labelled]) -> bool:
    """The first dated header block names two different days for when it was written."""
    first = created[0].block_index
    return len({labelled.day_ms for labelled in created if labelled.block_index == first}) > 1
