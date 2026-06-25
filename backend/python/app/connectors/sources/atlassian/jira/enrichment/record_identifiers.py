"""Lightweight Jira record identification helpers (no HTTP/service deps)."""

from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import Connectors
from app.models.entities import RecordType

JIRA_CONNECTORS: frozenset[Connectors] = frozenset({
    Connectors.JIRA,
    Connectors.JIRA_PERSONAL,
    Connectors.JIRA_DATA_CENTER,
    Connectors.JIRA_DATA_CENTER_PERSONAL,
})


def is_jira_connector(connector_name: Connectors | str | None) -> bool:
    if connector_name is None:
        return False
    if isinstance(connector_name, Connectors):
        return connector_name in JIRA_CONNECTORS
    try:
        return Connectors(connector_name) in JIRA_CONNECTORS
    except ValueError:
        return False


def is_jira_ticket_record(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    record_type = record.get("record_type") or record.get("recordType")
    if record_type != RecordType.TICKET.value:
        return False
    connector_name = record.get("connector_name") or record.get("connectorName")
    return is_jira_connector(connector_name)
