"""Build the vector-cleanup events published when a connector or KB is deleted.

Two events, chosen by whether the connector's points carry membership arrays:

``deleteConnectorEmbeddings`` — ``{orgId, connectorId, connectorName, recordGroupIds}``
    The normal path. The points are found by their own ``connectorIds`` array,
    so no record ids travel at all. ``recordGroupIds`` are the connector's own
    groups, which the graph delete removed: a point shared with a live
    connector survives, and without them it keeps pointing at groups that no
    longer exist. Bounded by group count, not record count.

``bulkDeleteRecords`` — ``{orgId, connectorId, connectorName, virtualRecordIds, ...}``
    Fallback for a connector whose points predate the membership arrays. Those
    points carry no membership, so a membership filter would never match them
    and they would be orphaned; the explicit id list is the only way to reach
    them.

    Used when the backfill has not finished **or** when it gave up: on
    exhaustion it sets ``vectorMembershipBackfilled`` **and**
    ``vectorMembershipBackfillExhausted`` together, so the completion flag
    alone cannot distinguish a healthy connector from one whose points it never
    managed to tag.

Consumers route on ``eventType``, never on which keys a payload happens to
carry. That matters during a rolling upgrade: an old consumer meets an event
type it does not know, fails the message and dead-letters it, which is visible
and replayable. Discriminating on a missing key instead would have it read the
new shape as an empty id list and *ack* it, silently leaving a deleted
connector's embeddings in place.

Deploy the indexing service before the connector service for that reason.

The fallback list is chunked because it is unbounded: one uuid4 costs 39 bytes
of JSON and Kafka's default request cap is 1 MiB, so a large connector used to
exceed it, fail the publish, and silently leave every embedding behind.
"""
from collections.abc import Sequence
from logging import Logger
from typing import Any

from app.config.constants.arangodb import EventTypes
from app.services.graph_db.vector_membership_queries import (
    can_use_membership_cleanup as _can_use_membership_cleanup,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# ~195 KB of ids per message, well inside the 1 MiB default request cap with
# room for the envelope.
MAX_VIRTUAL_RECORD_IDS_PER_EVENT = 5000


def build_connector_vector_cleanup_events(
    *,
    org_id: str,
    connector_id: str,
    vector_membership_backfilled: bool,
    vector_membership_backfill_exhausted: bool = False,
    connector_name: str | None = None,
    record_group_ids: Sequence[str] | None = None,
    virtual_record_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the events to publish for one connector/KB deletion."""
    # Same predicate the graph providers use to decide whether to collect a VRID
    # list at all, so the two can never disagree about which shape is safe.
    if _can_use_membership_cleanup({
        "vectorMembershipBackfilled": vector_membership_backfilled,
        "vectorMembershipBackfillExhausted": vector_membership_backfill_exhausted,
    }):
        return [
            _event(
                EventTypes.DELETE_CONNECTOR_EMBEDDINGS.value,
                {
                    "orgId": org_id,
                    "connectorId": connector_id,
                    "connectorName": connector_name,
                    "recordGroupIds": _unique_non_empty(record_group_ids),
                },
            )
        ]

    unique_ids = _unique_non_empty(virtual_record_ids)
    if not unique_ids:
        return []

    chunks = _chunks(unique_ids, MAX_VIRTUAL_RECORD_IDS_PER_EVENT)
    return [
        _event(
            EventTypes.BULK_DELETE_RECORDS.value,
            {
                "orgId": org_id,
                "connectorId": connector_id,
                "connectorName": connector_name,
                "virtualRecordIds": chunk,
                "totalRecords": len(chunk),
                # Identifies the chunk in a publish-failure log; a bare count
                # would say nothing about which ids went missing.
                "chunkIndex": index,
                "chunkCount": len(chunks),
            },
        )
        for index, chunk in enumerate(chunks)
    ]


def _event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventType": event_type,
        "timestamp": get_epoch_timestamp_in_ms(),
        "payload": payload,
    }


def _unique_non_empty(values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        )
    )


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def log_cleanup_publish_failure(
    logger: Logger, event: dict, subject: str, error: Exception
) -> None:
    """Report a vector-cleanup publish that failed.

    The graph records are already gone by this point, so the log is the only
    record of what was left behind — but a chunk holds up to 5000 uuids, and a
    broker outage fails every chunk. Rendering them all would build tens of
    megabytes of log text through blocking handlers on the event loop, so the
    count and the range go at error level and the ids at debug.
    """
    payload = event.get("payload", {}) or {}
    ids = payload.get("virtualRecordIds")
    event_type = event.get("eventType")
    if ids:
        where = (
            f"chunk {payload.get('chunkIndex', 0) + 1}/"
            f"{payload.get('chunkCount', 1)}"
        )
        detail = f"{len(ids)} virtual record id(s), {ids[0]}..{ids[-1]}"
    else:
        where = "connector-scoped event"
        detail = "every embedding for this connector"
    logger.error(
        f"❌ Failed to publish {event_type} ({where}) for {subject}: {error}. "
        f"Embeddings persist for {detail}"
    )
    if ids:
        logger.debug("Unpublished virtualRecordIds for %s: %s", subject, ids)
