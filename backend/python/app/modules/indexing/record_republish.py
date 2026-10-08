"""The event a waiting record is sent again with.

Shared by the stranded-record sweep and the lane upgrade's rescue, which both
re-send records that are already in line, so the two cannot drift apart on
what such an event carries.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import EventTypes

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["is_parked_duplicate", "record_event"]


def record_event(
    record: Mapping[str, object],
    *,
    record_key: str,
    connector_id: str,
    restored_upload: bool = False,
) -> tuple[str, dict[str, object]]:
    """``(event_type, payload)`` for re-sending a record that is waiting.

    An upload keeps version 0; a restored one was indexed before, so it is
    re-indexed rather than indexed afresh.
    """
    payload: dict[str, object] = {
        "recordId": record_key,
        "recordName": record.get("recordName"),
        "orgId": record.get("orgId"),
        "version": record.get("version", 0),
        "connectorName": record.get("connectorName"),
        "connectorId": connector_id,
        "extension": record.get("extension"),
        "mimeType": record.get("mimeType"),
        "origin": record.get("origin"),
        "recordType": record.get("recordType"),
        "virtualRecordId": record.get("virtualRecordId"),
    }
    version = int(payload.get("version", 0) or 0)  # type: ignore[call-overload]
    event_type = (
        EventTypes.REINDEX_RECORD.value
        if (version > 0 or restored_upload) and payload.get("virtualRecordId")
        else EventTypes.NEW_RECORD.value
    )
    return event_type, payload


def is_parked_duplicate(record: Mapping[str, object], *, restored_upload: bool = False) -> bool:
    """A duplicate parked behind an in-flight md5 twin: legitimately QUEUED with
    its message already acked, and released by the twin's completion, not by a
    re-send. A restored file still NOT_STARTED is not parked, though it keeps
    the checksum and content id it had."""
    return bool(
        record.get("md5Checksum") and record.get("virtualRecordId") and not restored_upload
    )
