"""Knowledge-base uploads take the dates their content states (CONTENT_DOCUMENT_DATES).

An upload's source timestamps are the upload time or the file's last-modified
time, so once named-entity extraction has read a document, a date its header
states replaces them. What the upload set is kept beside it, so a new version,
content that stops stating a date, or a revert puts it back. Connector records
keep the source system's dates.

The graph record is the only copy of these dates: search's date filters,
newest-first listings and record headers read it, and no vector payload carries
them, so there is nothing else to update.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.config.constants.arangodb import CollectionNames
from app.models.source_dates import (
    SOURCE_TIMESTAMP_ORIGIN_FIELD,
    UPLOAD_SOURCE_CREATED_FIELD,
    UPLOAD_SOURCE_MODIFIED_FIELD,
    UPLOAD_SOURCE_ORIGIN_FIELD,
    RecordDates,
    SourceTimestampOrigin,
    content_dates_replace,
    epoch_ms,
    is_kb_upload,
    parse_origin,
)
from app.modules.named_entities.document_dates import DocumentDates, find_document_dates

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.models.entities import Record
    from app.modules.named_entities.domain.models import NamedEntityExtraction
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_RECORDS = CollectionNames.RECORDS.value
_CREATED = "sourceCreatedAtTimestamp"
_MODIFIED = "sourceLastModifiedTimestamp"
# A file timestamp a day or more before the date its content states contradicts it.
_FILE_TIME_SLACK_MS = 86_400_000

Action = Literal["stamp", "date", "redate", "revert"]


@dataclass(frozen=True)
class DatesWrite:
    """``updates`` applied only while the record still holds ``expected``."""

    action: Action
    updates: dict[str, Any]
    expected: dict[str, Any]


def latest_upload_ms(doc: Mapping[str, Any]) -> int | None:
    """When the record's current version was uploaded, as near as the record
    tells it: a new version moves updatedAtTimestamp, not createdAtTimestamp."""
    times = [t for t in (epoch_ms(doc.get("createdAtTimestamp")), epoch_ms(doc.get("updatedAtTimestamp"))) if t]
    return max(times, default=None)


def _expected(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _CREATED: doc.get(_CREATED),
        _MODIFIED: doc.get(_MODIFIED),
        SOURCE_TIMESTAMP_ORIGIN_FIELD: doc.get(SOURCE_TIMESTAMP_ORIGIN_FIELD),
    }


def plan_dates_write(
    doc: Mapping[str, Any],
    dates: DocumentDates | None,
    replace: frozenset[SourceTimestampOrigin],
) -> DatesWrite | None:
    """The write that brings a records-collection document in line with the
    dates its content states; ``dates`` None means the content was read and
    states none. None when nothing is to change."""
    if not is_kb_upload(doc.get("connectorName"), doc.get("origin")):
        return None
    current = RecordDates.from_graph(doc)
    if current.origin is SourceTimestampOrigin.CONTENT:
        kept = parse_origin(doc.get(UPLOAD_SOURCE_ORIGIN_FIELD)) or SourceTimestampOrigin.UPLOAD
        if dates is None or kept not in replace:
            return plan_revert(doc)
        if (current.created_ms, current.modified_ms) == (dates.created_ms, dates.modified_ms):
            return None
        return DatesWrite("redate", {_CREATED: dates.created_ms, _MODIFIED: dates.modified_ms}, _expected(doc))
    if dates is None or current.origin not in replace or _contradicted(current, dates):
        if doc.get(SOURCE_TIMESTAMP_ORIGIN_FIELD) is None and current.origin is not None:
            return DatesWrite("stamp", {SOURCE_TIMESTAMP_ORIGIN_FIELD: current.origin.value}, _expected(doc))
        return None
    return DatesWrite(
        "date",
        {
            _CREATED: dates.created_ms,
            _MODIFIED: dates.modified_ms,
            SOURCE_TIMESTAMP_ORIGIN_FIELD: SourceTimestampOrigin.CONTENT.value,
            UPLOAD_SOURCE_CREATED_FIELD: doc.get(_CREATED),
            UPLOAD_SOURCE_MODIFIED_FIELD: doc.get(_MODIFIED),
            UPLOAD_SOURCE_ORIGIN_FIELD: current.origin.value,
        },
        _expected(doc),
    )


def plan_revert(doc: Mapping[str, Any]) -> DatesWrite | None:
    """Put back what the upload set on a record dated by its content."""
    if parse_origin(doc.get(SOURCE_TIMESTAMP_ORIGIN_FIELD)) is not SourceTimestampOrigin.CONTENT:
        return None
    kept = parse_origin(doc.get(UPLOAD_SOURCE_ORIGIN_FIELD)) or SourceTimestampOrigin.UPLOAD
    upload_time = doc.get("createdAtTimestamp")
    return DatesWrite(
        "revert",
        {
            _CREATED: doc.get(UPLOAD_SOURCE_CREATED_FIELD) or upload_time,
            _MODIFIED: doc.get(UPLOAD_SOURCE_MODIFIED_FIELD) or upload_time,
            SOURCE_TIMESTAMP_ORIGIN_FIELD: kept.value,
        },
        _expected(doc),
    )


def content_dates_apply(record: Record) -> bool:
    """Whether ``record`` takes the dates its content states: the switch is on
    and it is a knowledge-base upload."""
    return bool(content_dates_replace()) and is_kb_upload(record.connector_name, record.origin)


def _contradicted(current: RecordDates, dates: DocumentDates) -> bool:
    """A file last modified before the day its content says it was written."""
    if current.origin is not SourceTimestampOrigin.FILE or not current.modified_ms:
        return False
    return dates.created_ms > current.modified_ms + _FILE_TIME_SLACK_MS


async def apply_content_dates(
    graph_provider: IGraphDBProvider,
    record: Record,
    extraction: NamedEntityExtraction,
) -> str:
    """Write the dates ``record``'s content states once its extraction is done.

    Returns ``off`` (switched off, or not an upload), ``unread`` (extraction did
    not run, or ``record`` came without its blocks, so nothing is known),
    ``gone``, ``unchanged``, ``superseded`` (a new version landed meanwhile) or
    the write's action. Updates ``record`` to match.
    """
    if not content_dates_apply(record):
        return "off"
    if extraction.status in ("FAILED", "SKIPPED"):
        return "unread"
    # Blocks that were never loaded are not a document that states no date.
    blocks = getattr(record.block_containers, "blocks", None)
    if not blocks:
        return "unread"
    doc = await graph_provider.get_document(record.id, _RECORDS)
    if not doc:
        return "gone"
    dates = find_document_dates(extraction, blocks, uploaded_ms=latest_upload_ms(doc))
    write = plan_dates_write(doc, dates, content_dates_replace())
    if write is None:
        return "unchanged"
    if not await graph_provider.update_node_fields_if_match(record.id, _RECORDS, write.updates, write.expected):
        return "superseded"
    record.source_created_at = write.updates.get(_CREATED, record.source_created_at)
    record.source_updated_at = write.updates.get(_MODIFIED, record.source_updated_at)
    record.source_timestamp_origin = write.updates.get(SOURCE_TIMESTAMP_ORIGIN_FIELD, record.source_timestamp_origin)
    return write.action
