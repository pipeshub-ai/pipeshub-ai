"""The event a waiting record is sent again with.

Shared by the stranded-record sweep and the lane upgrade's rescue, which both
re-send records that are already in line, so the two cannot drift apart on
what such an event carries.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import EventTypes, OriginTypes, ProgressStatus
from app.connectors.sources.localKB.push.contract import is_pushed_revision
from app.services.graph_db.common.record_visibility import (
    is_live_record,
    is_searchable_record,
)
from app.services.graph_db.common.utils import RESTORED_AT_FIELD

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["is_parked_duplicate", "record_event", "reembeds_from_storage", "republish_eligible"]


def reembeds_from_storage(record: Mapping[str, object]) -> bool:
    """Whether a file back from the trash is re-embedded from its stored blocks.

    Its vectors went with the delete while its stored content stayed. A plain
    reindex compares the parsed blocks with that stored content, finds nothing
    changed and writes no vectors, so only a re-embed brings its content back to
    search. A file never indexed has no stored content and is indexed afresh.

    The stored blocks must also be of the current upload. Replacing a
    collection file sets ``externalRevisionId`` to the replace time (epoch ms)
    and leaves the old indexed copy in place until the reindex writes
    ``lastIndexTimestamp``; a trash in between drops that reindex. A revision
    newer than the last index, or one that cannot be compared, is indexed from
    the source.
    """
    if not (record.get("virtualRecordId") and is_searchable_record(record)):
        return False
    revision = record.get("externalRevisionId")
    if revision in (None, ""):
        return True
    try:
        return int(str(revision)) <= int(str(record.get("lastIndexTimestamp")))
    except (TypeError, ValueError):
        return False


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
    if restored_upload and event_type == EventTypes.REINDEX_RECORD.value and reembeds_from_storage(record):
        payload["vectorDbOnly"] = True
    return event_type, payload


def is_parked_duplicate(record: Mapping[str, object], *, restored_upload: bool = False) -> bool:
    """A duplicate parked behind an in-flight md5 twin: legitimately QUEUED with
    its message already acked, and released by the twin's completion, not by a
    re-send. A restored file still NOT_STARTED is not parked, though it keeps
    the checksum and content id it had."""
    return bool(
        record.get("md5Checksum") and record.get("virtualRecordId") and not restored_upload
    )


def republish_eligible(record: Mapping[str, object], status: str) -> tuple[bool, bool]:
    """``(eligible, restored_upload)`` for a record found waiting in ``status``.

    A connector's record is always eligible. An upload is left alone, since a new one waits on
    its file reaching storage, except a restored one (its content is stored and the restore was
    the only thing that queued it) and a pushed one: a push writes the row only after its
    content is stored, so a row still waiting means the publish after the commit was lost.
    """
    live = is_live_record(record)
    restored_upload = (
        status == ProgressStatus.NOT_STARTED.value and bool(record.get(RESTORED_AT_FIELD)) and live
    )
    pushed = (
        record.get("origin") == OriginTypes.UPLOAD.value
        and is_pushed_revision(record.get("externalRevisionId"))  # type: ignore[arg-type]
        and live
        and status in (ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value)
    )
    eligible = record.get("origin") == OriginTypes.CONNECTOR.value or restored_upload or pushed
    return eligible, restored_upload
