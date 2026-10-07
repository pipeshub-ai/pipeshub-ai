"""An in-memory graph of records and connector apps, for the connector-off rule.

Shaped like the Neo4j provider, which is what installs ship: documents carry
``id`` and a ``_key`` copy of it, a field asked for in ``return_fields`` that a
node lacks comes back as None, a null in an update removes the property (as
``SET +=`` does), ``batch_update_nodes`` updates only nodes that exist and
answers False if any did not, and every read returns copies. Each call is
counted, so a test can assert how many round trips a batch cost.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.config.constants.arangodb import CollectionNames, OriginTypes, ProgressStatus

__all__ = ["FakeConnectorGraph"]


class FakeConnectorGraph:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.apps: dict[str, dict[str, Any]] = {}
        self.calls: Counter[str] = Counter()
        self.batch_updates: list[list[dict[str, Any]]] = []
        # Raised by every call naming that collection, to stand in for a graph
        # that cannot be reached.
        self.fail_reads: set[str] = set()
        self.fail_writes = False

    def add_connector(self, connector_id: str, *, active: bool = True) -> None:
        self.apps[connector_id] = {"id": connector_id, "_key": connector_id, "isActive": active}

    def set_active(self, connector_id: str, active: bool) -> None:
        self.apps[connector_id]["isActive"] = active

    def remove_connector(self, connector_id: str) -> None:
        self.apps.pop(connector_id, None)

    def add_record(self, record_id: str, connector_id: str, **fields: object) -> dict[str, Any]:
        record = {
            "id": record_id,
            "_key": record_id,
            "connectorId": connector_id,
            "origin": OriginTypes.CONNECTOR.value,
            "orgId": "org-1",
            "recordName": f"{record_id}.txt",
            "indexingStatus": ProgressStatus.QUEUED.value,
            "extractionStatus": ProgressStatus.NOT_STARTED.value,
            **fields,
        }
        self.records[record_id] = record
        return record

    def _collection(self, collection: str) -> dict[str, dict[str, Any]]:
        if collection == CollectionNames.RECORDS.value:
            return self.records
        if collection == CollectionNames.APPS.value:
            return self.apps
        raise AssertionError(f"unexpected collection {collection}")

    def _check_read(self, collection: str) -> None:
        if collection in self.fail_reads:
            raise ConnectionError(f"graph unavailable reading {collection}")

    async def get_document(
        self, key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False
    ) -> dict[str, Any] | None:
        self.calls[f"get_document:{collection}"] += 1
        try:
            self._check_read(collection)
        except ConnectionError:
            if raise_on_error:
                raise
            return None
        doc = self._collection(collection).get(key)
        return dict(doc) if doc is not None else None

    async def get_nodes_by_field_in(
        self,
        collection: str,
        field_name: str,
        field_values: list[Any],
        return_fields: list[str] | None = None,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls[f"get_nodes_by_field_in:{collection}"] += 1
        try:
            self._check_read(collection)
        except ConnectionError:
            if raise_on_error:
                raise
            return []
        wanted = set(field_values)
        out = []
        for doc in self._collection(collection).values():
            if doc.get(field_name) not in wanted:
                continue
            if return_fields:
                node = {f: doc.get(f) for f in return_fields}
                node["_key"] = node.get("id")
            else:
                node = dict(doc)
            out.append(node)
        return out

    def _apply(self, doc: dict[str, Any], updates: dict[str, Any]) -> None:
        for name, value in updates.items():
            if name in ("id", "_key"):
                continue
            if value is None:
                doc.pop(name, None)
            else:
                doc[name] = value

    async def update_node(
        self, key: str, collection: str, node_updates: dict[str, Any], transaction: str | None = None
    ) -> bool:
        self.calls[f"update_node:{collection}"] += 1
        if self.fail_writes:
            raise ConnectionError("graph unavailable writing")
        doc = self._collection(collection).get(key)
        if doc is None:
            return False
        self._apply(doc, node_updates)
        return True

    async def compare_and_set_indexing_status(
        self, record_ids: list[str], expected: str, new_status: str, transaction: str | None = None
    ) -> list[str]:
        """As both providers do: one conditional write, the ids it changed, and
        an empty list rather than an error when the write fails."""
        self.calls["compare_and_set_indexing_status"] += 1
        if self.fail_writes:
            return []
        swapped = []
        for record_id in dict.fromkeys(record_ids):
            doc = self.records.get(record_id)
            if doc is not None and doc.get("indexingStatus") == expected:
                doc["indexingStatus"] = new_status
                swapped.append(record_id)
        return swapped

    async def batch_update_nodes(
        self, nodes: list[dict[str, Any]], collection: str, transaction: str | None = None
    ) -> bool:
        self.calls[f"batch_update_nodes:{collection}"] += 1
        if self.fail_writes:
            raise ConnectionError("graph unavailable writing")
        self.batch_updates.append([dict(n) for n in nodes])
        store = self._collection(collection)
        all_found = True
        for node in nodes:
            doc = store.get(node.get("id") or node.get("_key"))
            if doc is None:
                all_found = False
                continue
            self._apply(doc, node)
        return all_found
