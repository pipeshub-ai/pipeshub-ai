"""The ``record-title-events`` message: a record's name, published before its content is indexed.

Connectors publish it so a record is findable by name straight away; the indexing service's
title consumer turns it into the record's entity point. The message is only a hint: the
consumer re-reads the record from the graph and writes what it finds there.
"""

from __future__ import annotations

from typing import Any

# What a title point embeds is the name only; a longer one is cut here and again by the consumer.
RECORD_TITLE_MAX_CHARS = 512

EVENT_TYPE = "recordTitle"
UPSERT = "upsert"
DELETE = "delete"


def clean_record_title(name: object) -> str | None:
    """The name to embed, cut to ``RECORD_TITLE_MAX_CHARS``, or None when it is blank or not text."""
    if not isinstance(name, str) or not name.strip():
        return None
    return name[:RECORD_TITLE_MAX_CHARS]


def build_record_title_message(
    *,
    org_id: str | None,
    record_id: str,
    record_name: str | None,
    connector_id: str | None,
    record_group_id: str | None,
    event_type: str,
    timestamp: int,
    attempt: int = 0,
) -> dict[str, Any]:
    message = {
        "eventType": EVENT_TYPE,
        "timestamp": timestamp,
        "payload": {
            "orgId": org_id,
            "recordId": record_id,
            "recordName": record_name,
            "connectorId": connector_id,
            "recordGroupId": record_group_id,
            "eventType": event_type,
            "timestamp": timestamp,
        },
    }
    if attempt:  # set only by the consumer, when it puts a failed event back on the topic
        message["payload"]["attempt"] = attempt
    return message
