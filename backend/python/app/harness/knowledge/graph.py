"""The slice of ``IGraphDBProvider`` publications use, typed at the boundary.

The provider answers untyped documents (``dict``, ``list[dict]``); everything here reads them as ``object`` and
narrows through :func:`doc`, so a backend answering an unexpected shape grants nothing rather than raising deep
inside a retrieval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from app.models.entities import Record

Doc = dict[str, object]


class PublicationGraph(Protocol):
    async def batch_upsert_nodes(self, nodes: list[Doc], collection: str) -> object: ...

    async def batch_create_edges(self, edges: list[Doc], collection: str) -> object: ...

    async def delete_nodes(self, keys: list[str], collection: str) -> object: ...

    async def delete_edges_to(self, to_id: str, to_collection: str, collection: str) -> object: ...

    async def update_node(self, key: str, collection: str, node_updates: Doc) -> object: ...

    async def get_document(self, document_key: str, collection: str, *, raise_on_error: bool = ...) -> object: ...

    async def get_nodes_by_filters(self, collection: str, filters: Doc) -> object: ...

    async def get_documents_paginated(
        self, collection: str, skip: int = ..., limit: int = ..., filters: Doc | None = ..., *, raise_on_error: bool = ...
    ) -> object: ...

    async def get_edge(
        self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str
    ) -> object: ...

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = ...) -> object: ...

    async def get_user_teams(self, user_key: str, *, page: int = ..., limit: int = ...) -> tuple[object, int]: ...

    async def get_user_kb_permission(self, kb_id: str, user_id: str) -> object: ...

    async def get_records_by_record_group(
        self,
        record_group_id: str,
        connector_id: str,
        org_id: str,
        depth: int,
        *,
        limit: int | None = ...,
        after_key: str | None = ...,
    ) -> list[Record]: ...

    async def get_virtual_record_ids_for_record_ids(self, record_ids: list[str]) -> object: ...


def graph_of(provider: object) -> PublicationGraph:
    """``IGraphDBProvider`` read through the typed slice (its methods match; their returns are untyped)."""
    return cast("PublicationGraph", provider)


def doc(value: object) -> Doc | None:
    return cast("Doc", value) if isinstance(value, dict) else None


def docs(value: object) -> list[Doc]:
    if not isinstance(value, list):
        return []
    return [d for d in (doc(item) for item in cast("list[object]", value)) if d is not None]


def text(document: Doc, *keys: str) -> str | None:
    for key in keys:
        value = document.get(key)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = ["Doc", "PublicationGraph", "doc", "docs", "graph_of", "text"]
