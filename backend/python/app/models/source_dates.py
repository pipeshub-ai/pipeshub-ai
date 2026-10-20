"""Where a record's source timestamps come from, and how the model is told.

A connector's record carries the source system's own created and modified
times. A knowledge-base upload carries what the upload gave it: the file's
last-modified time when the uploader sent one, otherwise the upload time.
Neither is a date the document states, so the model is not shown either as
the document's creation date.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import Connectors, OriginTypes

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class SourceTimestampOrigin(str, Enum):
    UPLOAD = "upload"
    FILE = "file"


SOURCE_TIMESTAMP_ORIGIN_FIELD = "sourceTimestampOrigin"

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


@dataclass(frozen=True)
class RecordDates:
    """A record's two source timestamps and what they are.

    ``origin`` is None for a connector's record: the source system's own dates.
    """

    origin: SourceTimestampOrigin | None
    created_ms: int | None
    modified_ms: int | None
    uploaded_ms: int | None = None

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
        # Uploads indexed before the origin was stored are told apart by clock.
        kind = parse_origin(stored_origin) or upload_origin(created, uploaded)
        return cls(kind, created, modified, uploaded)

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
        if self.origin is SourceTimestampOrigin.UPLOAD:
            return self.uploaded_ms or self.created_ms
        return self.uploaded_ms

    def header_lines(self, full: Callable[[int | None], str]) -> list[str]:
        """The date lines of a record header; ``full`` renders a timestamp."""
        if self.origin is None:
            return [f"Created At: {full(self.created_ms)}", f"Last Updated At: {full(self.modified_ms)}"]
        lines: list[str] = []
        if self.origin is SourceTimestampOrigin.FILE and (self.modified_ms or self.created_ms):
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
        if self.origin is SourceTimestampOrigin.FILE and (self.modified_ms or self.created_ms):
            return "fileModified", self.modified_ms or self.created_ms
        return ("uploaded", self.upload_time) if self.upload_time else None
