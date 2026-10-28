"""Where a record's source timestamps come from, and how the model is told.

A connector's record carries the source system's own created and modified
times. A knowledge-base upload carries what the upload gave it: the file's
last-modified time when the uploader sent one, otherwise the upload time.
Neither is a date the document states, so the model is not shown either as
the document's creation date. With CONTENT_DOCUMENT_DATES on, a date the
upload's content states replaces them (app.modules.indexing.content_dates).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import Connectors, OriginTypes
from app.utils.time_conversion import format_day_for_llm

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class SourceTimestampOrigin(str, Enum):
    UPLOAD = "upload"
    FILE = "file"
    CONTENT = "content"


SOURCE_TIMESTAMP_ORIGIN_FIELD = "sourceTimestampOrigin"
# What the upload had set, kept when the content's dates replace it.
UPLOAD_SOURCE_CREATED_FIELD = "uploadSourceCreatedAtTimestamp"
UPLOAD_SOURCE_MODIFIED_FIELD = "uploadSourceLastModifiedTimestamp"
UPLOAD_SOURCE_ORIGIN_FIELD = "uploadSourceTimestampOrigin"

CONTENT_DOCUMENT_DATES_ENV_VAR = "CONTENT_DOCUMENT_DATES"
# A time_range flag: leave out an upload dated only by its upload instead of
# bounding it by that upload (upload_times_undated). A timeline places records
# by date, so its period and span searches set it.
DATED_ONLY_TIME_RANGE_KEY = "dated_only"

# Node stamps a file sent without a last-modified time while it receives the
# upload, a moment before it stamps the record's createdAtTimestamp.
UPLOAD_CLOCK_WINDOW_MS = 120_000


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def epoch_ms(value: object) -> int | None:
    """A stored timestamp, or None for a missing, zero or non-numeric one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def is_kb_upload(connector_name: object, origin: object) -> bool:
    return (
        _enum_value(connector_name) == Connectors.KNOWLEDGE_BASE.value
        and _enum_value(origin) == OriginTypes.UPLOAD.value
    )


def upload_origin(source_ms: object, uploaded_ms: object) -> SourceTimestampOrigin:
    """Whether a timestamp an upload set is the upload time or the file's own.

    Unknown counts as the upload time: it is then shown as one, never as a
    date of the document.
    """
    source, uploaded = epoch_ms(source_ms), epoch_ms(uploaded_ms)
    if source is None or uploaded is None or abs(uploaded - source) <= UPLOAD_CLOCK_WINDOW_MS:
        return SourceTimestampOrigin.UPLOAD
    return SourceTimestampOrigin.FILE


def parse_origin(value: object) -> SourceTimestampOrigin | None:
    try:
        return SourceTimestampOrigin(_enum_value(value))
    except ValueError:
        return None


def _inferred_origin(created: int | None, modified: int | None, uploaded: int | None) -> SourceTimestampOrigin:
    """The origin of an upload stored before it was kept, told apart by clock.

    It describes the latest version's time, the one a header shows, as a stored
    origin does. Only the first version's upload time is on record
    (createdAtTimestamp); a later version's time is a file's only if it is older
    than that, since updatedAtTimestamp also moves on a rename.
    """
    if modified is None or modified == created:
        return upload_origin(created, uploaded)
    if uploaded is not None and modified < uploaded - UPLOAD_CLOCK_WINDOW_MS:
        return SourceTimestampOrigin.FILE
    return SourceTimestampOrigin.UPLOAD


def content_dates_replace() -> frozenset[SourceTimestampOrigin]:
    """The upload-set timestamps a date the content states replaces.

    ``CONTENT_DOCUMENT_DATES``: unset or anything else is off; ``true`` replaces
    only the upload time standing in for a date; ``all`` also replaces the file
    last-modified time the uploader sent.
    """
    raw = os.getenv(CONTENT_DOCUMENT_DATES_ENV_VAR, "").strip().lower()
    if raw == "all":
        return frozenset({SourceTimestampOrigin.UPLOAD, SourceTimestampOrigin.FILE})
    if raw == "true":
        return frozenset({SourceTimestampOrigin.UPLOAD})
    return frozenset()


def upload_times_undated() -> bool:
    """Whether a record dated only by its upload counts as undated: bounded only
    by its upload in date filters (written then or earlier) and put last in
    newest-first listings. On with content dates."""
    return bool(content_dates_replace())


@dataclass(frozen=True)
class RecordDates:
    """A record's two source timestamps and what they are.

    ``origin`` is None for a connector's record: the source system's own dates.
    """

    origin: SourceTimestampOrigin | None
    created_ms: int | None
    modified_ms: int | None
    uploaded_ms: int | None = None
    # No stored origin and no upload time to tell the source times by: they are
    # the upload's, but whether the upload's time or the file's is unknown.
    upload_time_unknown: bool = False

    @classmethod
    def resolve(
        cls,
        *,
        connector_name: object,
        origin: object,
        stored_origin: object,
        created_ms: object,
        modified_ms: object,
        uploaded_ms: object,
    ) -> RecordDates:
        created, modified = epoch_ms(created_ms), epoch_ms(modified_ms)
        if not is_kb_upload(connector_name, origin):
            return cls(None, created, modified)
        uploaded = epoch_ms(uploaded_ms)
        stored = parse_origin(stored_origin)
        if stored is not None:
            return cls(stored, created, modified, uploaded)
        return cls(
            _inferred_origin(created, modified, uploaded), created, modified, uploaded,
            upload_time_unknown=uploaded is None,
        )

    @classmethod
    def from_graph(cls, doc: Mapping[str, Any]) -> RecordDates:
        """From a records-collection document (camelCase fields)."""
        return cls.resolve(
            connector_name=doc.get("connectorName"),
            origin=doc.get("origin"),
            stored_origin=doc.get(SOURCE_TIMESTAMP_ORIGIN_FIELD),
            created_ms=doc.get("sourceCreatedAtTimestamp"),
            modified_ms=doc.get("sourceLastModifiedTimestamp"),
            uploaded_ms=doc.get("createdAtTimestamp"),
        )

    @property
    def upload_time(self) -> int | None:
        """When the version shown was uploaded, as near as the record says."""
        if self.origin is not SourceTimestampOrigin.UPLOAD:
            return self.uploaded_ms
        if self.upload_time_unknown:
            return None
        # A new version sent without a file time keeps its own upload time there.
        later = self.modified_ms if self.modified_ms and self.modified_ms != self.created_ms else None
        return later or self.uploaded_ms or self.created_ms

    def header_lines(self, full: Callable[[int | None], str]) -> list[str]:
        """The date lines of a record header; ``full`` renders a timestamp."""
        if self.origin is None:
            return [f"Created At: {full(self.created_ms)}", f"Last Updated At: {full(self.modified_ms)}"]
        lines: list[str] = []
        if self.origin is SourceTimestampOrigin.CONTENT and self.created_ms:
            # Stored at midnight UTC of the day the document states.
            created = format_day_for_llm(self.created_ms, calendar_date=True)
            lines.append(f"Document Date: {created} (stated in the document)")
            modified = format_day_for_llm(self.modified_ms, calendar_date=True) if self.modified_ms else created
            if modified != created:
                lines.append(f"Last Updated: {modified} (stated in the document)")
        elif self.origin is SourceTimestampOrigin.FILE and (self.modified_ms or self.created_ms):
            lines.append(
                f"File Last Modified: {full(self.modified_ms or self.created_ms)} "
                "(the uploaded file's timestamp, not a document date)"
            )
        if self.upload_time:
            lines.append(f"Uploaded At: {full(self.upload_time)} (upload time, not a document date)")
        return lines

    def listing_date(self, fallback_ms: object = None) -> tuple[str, int] | None:
        """The key and date a one-line listing shows: ``("modified", ms)`` for a
        connector's record, ``fallback_ms`` when it has no source date."""
        if self.origin is None:
            modified = self.modified_ms or epoch_ms(fallback_ms)
            return ("modified", modified) if modified else None
        if self.origin is SourceTimestampOrigin.CONTENT and self.created_ms:
            return "dated", self.created_ms
        file_ms = self.modified_ms or self.created_ms
        if self.origin is SourceTimestampOrigin.FILE and file_ms:
            return "fileModified", file_ms
        return ("uploaded", self.upload_time) if self.upload_time else None
