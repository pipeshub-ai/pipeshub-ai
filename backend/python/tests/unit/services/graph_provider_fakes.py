"""In-memory `IGraphDBProvider` fakes that model the two real backends'
*differing* semantics, so an adapter can be proved portable without a live
ArangoDB or Neo4j.

The two backends are not interchangeable at the payload level, and the
differences are exactly where portability bugs hide:

* **Property shape.** Neo4j node properties may hold primitives or arrays of
  primitives, never nested maps or arrays of maps. Arango accepts anything
  JSON-serialisable. Both fakes assert the Neo4j rule, because the goal is one
  payload shape that both backends accept -- an adapter that only satisfies
  Arango is a latent Neo4j outage.
* **Nulls.** Neo4j's `SET n.p = null` *removes* the property; Arango stores a
  JSON null. An adapter that writes null for "empty" therefore reads back an
  absent key on one backend and `None` on the other, and only the second
  breaks Pydantic validation of a non-Optional field.
* **Identity.** Arango returns `_key`/`_id`/`_rev` alongside the document;
  Neo4j has no such properties, only whatever `id` the node carries.

Parametrise over `GRAPH_PROVIDER_FAKES` to run an adapter's round-trip suite
against both.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "GRAPH_PROVIDER_FAKES",
    "ArangoSemanticsGraphProvider",
    "BaseFakeGraphProvider",
    "FakeGraphProvider",
    "Neo4jSemanticsGraphProvider",
    "assert_neo4j_safe",
]

_NEO4J_PRIMITIVES = (str, int, float, bool)


def assert_neo4j_safe(doc: dict[str, Any], context: str) -> None:
    """Neo4j node properties may only be primitives or arrays of primitives."""
    for key, value in doc.items():
        if value is None or isinstance(value, _NEO4J_PRIMITIVES):
            continue
        assert isinstance(value, list), (
            f"{context}: property {key!r} is {type(value).__name__}, not primitive/array"
        )
        for item in value:
            assert isinstance(item, _NEO4J_PRIMITIVES), (
                f"{context}: array property {key!r} contains {type(item).__name__} "
                f"-- Neo4j arrays must hold primitives only (no maps, no nulls)"
            )


class BaseFakeGraphProvider:
    """Storage and query behaviour common to both backends."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _col(self, name: str) -> dict[str, dict[str, Any]]:
        return self._collections.setdefault(name, {})

    def seed(self, collection: str, doc: dict[str, Any]) -> None:
        """Insert a document without the write-path guard, for staging rows an
        older adapter version would have written."""
        self._col(collection)[str(doc.get("id") or doc.get("_key"))] = dict(doc)

    def _store(self, node: dict[str, Any]) -> dict[str, Any]:
        """Backend-specific normalisation applied on write."""
        raise NotImplementedError

    def _read(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Backend-specific decoration applied on read."""
        raise NotImplementedError

    async def get_document(
        self, document_key: str, collection: str, transaction: str | None = None,
    ) -> dict | None:
        doc = self._col(collection).get(str(document_key))
        return self._read(doc) if doc is not None else None

    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: dict[str, Any],
        return_fields: list[str] | None = None,
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            self._read(doc) for doc in self._col(collection).values()
            if all(doc.get(k) == v for k, v in filters.items())
        ]
        if return_fields is None:
            return rows
        return [self._project(row, return_fields) for row in rows]

    def _project(self, row: dict[str, Any], return_fields: list[str]) -> dict[str, Any]:
        return {field: row.get(field) for field in return_fields}

    async def get_documents_paginated(
        self,
        collection: str,
        skip: int = 0,
        limit: int = 50,
        filters: dict[str, Any] | None = None,
        sort_field: str | None = None,
        sort_desc: bool = False,
        transaction: str | None = None,
        raise_on_error: bool = False,
    ) -> list[dict]:
        rows = [
            self._read(doc) for doc in self._col(collection).values()
            if all(doc.get(k) == v for k, v in (filters or {}).items())
        ]
        if sort_field:
            # Sorting happens here, in the "database", so an adapter that
            # re-sorts the returned page client-side is visibly wrong.
            rows.sort(key=lambda d: d.get(sort_field) or 0, reverse=sort_desc)
        return rows[skip : skip + limit]

    async def batch_upsert_nodes(
        self, nodes: list[dict[str, Any]], collection: str, transaction: str | None = None,
    ) -> bool:
        col = self._col(collection)
        for node in nodes:
            assert_neo4j_safe(node, f"batch_upsert_nodes({collection})")
            key = str(node.get("id") or node.get("_key"))
            col[key] = self._store(node)
        return True

    async def delete_nodes(
        self, keys: list[str], collection: str, transaction: str | None = None,
    ) -> bool:
        col = self._col(collection)
        for key in keys:
            col.pop(str(key), None)
        return True


class ArangoSemanticsGraphProvider(BaseFakeGraphProvider):
    """Stores documents verbatim, nulls included, and returns Arango's
    system attributes alongside them."""

    def _store(self, node: dict[str, Any]) -> dict[str, Any]:
        return dict(node)

    def _read(self, doc: dict[str, Any]) -> dict[str, Any]:
        key = str(doc.get("id") or doc.get("_key"))
        return {**doc, "_key": key, "_id": f"c/{key}", "_rev": "_rev1"}


class Neo4jSemanticsGraphProvider(BaseFakeGraphProvider):
    """Drops null-valued properties on write and exposes no `_key`."""

    def _store(self, node: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in node.items() if v is not None}

    def _read(self, doc: dict[str, Any]) -> dict[str, Any]:
        return dict(doc)

    def _project(self, row: dict[str, Any], return_fields: list[str]) -> dict[str, Any]:
        for field in return_fields:
            assert field in row, (
                f"return_fields asked for {field!r}, which no Neo4j node carries. "
                f"Real Neo4j answers with a column of nulls rather than failing, so "
                f"a caller counting or dereferencing the result is silently wrong."
            )
        return {field: row[field] for field in return_fields}


# Kept as the historical name used by the task-store contract suite.
FakeGraphProvider = ArangoSemanticsGraphProvider

GRAPH_PROVIDER_FAKES = (ArangoSemanticsGraphProvider, Neo4jSemanticsGraphProvider)
