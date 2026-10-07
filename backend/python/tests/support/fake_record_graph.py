"""An in-memory graph holding only what the stranded-record sweep reads and writes.

Records by key, read a page at a time by ``indexingStatus`` in key order, and
updated field by field. Returns copies, as a real store does, so a caller that
mutates what it read does not change what is stored. A null removes the
property, as Neo4j's ``SET +=`` does. Every connector is live.
"""
from __future__ import annotations

from app.config.constants.arangodb import CollectionNames, ProgressStatus

__all__ = ["FakeRecordGraph"]


class FakeRecordGraph:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def add_queued(self, key: str, connector_id: str, queued_at_ms: int, **fields: object) -> None:
        self.records[key] = {
            "_key": key,
            "connectorId": connector_id,
            "origin": "CONNECTOR",
            "recordName": key,
            "orgId": "org-1",
            "version": 0,
            "indexingStatus": ProgressStatus.QUEUED.value,
            "queuedAtTimestamp": queued_at_ms,
            **fields,
        }

    def mark_indexed(self, key: str) -> None:
        self.records[key]["indexingStatus"] = ProgressStatus.COMPLETED.value

    def still_queued(self) -> list[str]:
        return sorted(
            key for key, record in self.records.items()
            if record["indexingStatus"] == ProgressStatus.QUEUED.value
        )

    async def get_documents_paginated(
        self,
        collection: str,
        skip: int = 0,
        limit: int = 50,
        filters: dict | None = None,
        sort_field: str | None = None,
        raise_on_error: bool = False,
    ) -> list[dict]:
        assert collection == CollectionNames.RECORDS.value
        status = (filters or {}).get("indexingStatus")
        matching = sorted(
            (r for r in self.records.values() if r["indexingStatus"] == status),
            key=lambda r: r["_key"],
        )
        return [dict(r) for r in matching[skip:skip + limit]]

    async def get_document(self, key: str, collection: str, **_kwargs: object) -> dict:
        assert collection == CollectionNames.APPS.value
        return {"_key": key, "isActive": True}

    async def update_node(self, key: str, collection: str, fields: dict) -> bool:
        assert collection == CollectionNames.RECORDS.value
        record = self.records[key]
        for name, value in fields.items():
            if value is None:
                record.pop(name, None)
            else:
                record[name] = value
        return True
