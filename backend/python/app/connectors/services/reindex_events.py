"""The '*.reindex' sync event: one shape for every caller that asks a connector to reindex."""
from __future__ import annotations

from typing import Any

__all__ = ["build_reindex_event"]


def build_reindex_event(
    *,
    event_type: str,
    org_id: str,
    connector_id: str,
    connector_name: str | None = None,
    record_id: str | None = None,
    record_group_id: str | None = None,
    depth: int | None = None,
    user_key: str | None = None,
    status_filters: list[str] | None = None,
    stages: list[str] | None = None,
) -> dict:
    """Build the {eventType, topic, payload} envelope for a '*.reindex' sync-event.

    Shared by the record-group and connector-wide reindex routes so the payload
    shape stays consistent (single-record reindex builds its own payload inside
    reindex_single_record, which has a KB depth==0 'newRecord' special-case that
    doesn't fit this generic shape).
    """
    payload: dict[str, Any] = {"orgId": org_id, "connectorId": connector_id}
    if connector_name is not None:
        payload["connector"] = connector_name
    if record_id is not None:
        payload["recordId"] = record_id
    if record_group_id is not None:
        payload["recordGroupId"] = record_group_id
    if depth is not None:
        payload["depth"] = depth
    if user_key is not None:
        payload["userKey"] = user_key
    if status_filters:
        payload["statusFilters"] = status_filters
    if stages:
        payload["stages"] = stages
    return {"eventType": event_type, "topic": "sync-events", "payload": payload}
