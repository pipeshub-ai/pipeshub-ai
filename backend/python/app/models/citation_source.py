"""Live records: files a tool read straight from a connector (P1b C-18).

Such a tool builds a temporary record so the file renders like an indexed
one. The record is never saved, so its id carries a prefix no record key has:
wherever a citation is checked, a live id is never taken for a record id.
The typed source a shared viewer's access is decided on is the contract's
``PipeshubCitationSource``; a live one names the connector and the item.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import Connectors

if TYPE_CHECKING:
    from collections.abc import Mapping

LIVE_RECORD_ID_PREFIX = "live:"
LIVE_SOURCE_KIND = "live"

_GOOGLE_DRIVE = (Connectors.GOOGLE_DRIVE.value, Connectors.GOOGLE_DRIVE_WORKSPACE.value)
# Connector kinds whose records can be the indexed copy of an item a live
# tool read: Google Drive has an individual and a workspace connector.
_INDEXED_BY: Mapping[str, tuple[str, ...]] = MappingProxyType(dict.fromkeys(_GOOGLE_DRIVE, _GOOGLE_DRIVE))


def live_record_id() -> str:
    return f"{LIVE_RECORD_ID_PREFIX}{uuid4().hex}"


def is_live_record_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith(LIVE_RECORD_ID_PREFIX)


def indexed_copy_connectors(connector: str) -> tuple[str, ...]:
    """The connector kinds whose records may index what ``connector`` fetched live."""
    return _INDEXED_BY.get(connector, (connector,))


def live_citation_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    """What a live record's citations add to their metadata."""
    return {
        "sourceKind": LIVE_SOURCE_KIND,
        "externalRecordId": record.get("external_record_id") or "",
    }


def count_live_citations(citations: list[Mapping[str, Any]]) -> int:
    return sum(
        1 for c in citations if (c.get("metadata") or {}).get("sourceKind") == LIVE_SOURCE_KIND
    )


__all__ = [
    "LIVE_RECORD_ID_PREFIX",
    "LIVE_SOURCE_KIND",
    "count_live_citations",
    "indexed_copy_connectors",
    "is_live_record_id",
    "live_citation_metadata",
    "live_record_id",
]
