"""`FilteredRetrievalBridge`: native filter hits -> permission-gated PipesHub
records -> (optionally) semantic content search scoped to that universe.

This is where a `FilteredSearchUniverse` (external IDs only, no content,
not permission-checked) turns into either:
  - a plain listing of accessible matching records (no `content_query`), or
  - PipesHub's own retrieval, scoped via the existing
    `RetrievalService.search_with_filters(virtual_record_ids_from_tool=...)`
    parameter (no retrieval-service changes needed).

Permission gating happens HERE, per record, via the same
`get_knowledge_hub_node_access` check `RecordResolver` uses — deliberately
NOT relying on `search_with_filters`'s own accessible-set computation,
because that call only *intersects* the accessible set when
`virtual_record_ids_from_tool` is absent; when the parameter IS supplied it
is used as-is (see `retrieval_service.py::search_with_filters`). Passing
un-gated virtual_record_ids there would leak content across a permission
boundary, so this bridge must not skip its own gate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.actions.filtered_search.models import FilteredSearchUniverse
    from app.models.entities import Record
    from app.modules.retrieval.retrieval_service import RetrievalService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)


@dataclass
class BridgedResult:
    """Final, permission-gated output of a filter-then-retrieve call."""

    accessible_records: list["Record"] = field(default_factory=list)
    denied_count: int = 0
    unresolved_external_ids: list[str] = field(default_factory=list)
    content_search_ran: bool = False
    retrieval_response: dict[str, Any] | None = None


class FilteredRetrievalBridge:
    def __init__(
        self,
        graph_provider: "IGraphDBProvider",
        retrieval_service: "RetrievalService | None",
        folder_mime_types: list[str],
    ) -> None:
        self._graph = graph_provider
        self._retrieval = retrieval_service
        self._folder_mime_types = folder_mime_types

    async def resolve_and_gate(
        self,
        universe: FilteredSearchUniverse,
        connector_id: str,
        user_key: str,
        org_id: str,
    ) -> BridgedResult:
        """Batch-resolve external IDs and drop anything the user cannot access."""
        external_ids = [r.external_id for r in universe.records if r.external_id]
        if not external_ids:
            return BridgedResult()

        records = await self._graph.get_records_by_external_ids(
            connector_id=connector_id, external_ids=external_ids,
        )
        resolved_ids = {r.external_record_id for r in records if r.external_record_id}
        unresolved = [eid for eid in external_ids if eid not in resolved_ids]

        accessible: list[Record] = []
        denied = 0
        for record in records:
            if not record.id:
                continue
            node = await self._graph.get_knowledge_hub_node_access(
                node_id=record.id,
                user_key=user_key,
                org_id=org_id,
                folder_mime_types=self._folder_mime_types,
            )
            if node is not None:
                accessible.append(record)
            else:
                denied += 1

        return BridgedResult(
            accessible_records=accessible,
            denied_count=denied,
            unresolved_external_ids=unresolved,
        )

    async def search_content_within(
        self,
        bridged: BridgedResult,
        content_query: str,
        user_id: str,
        org_id: str,
        limit: int = 50,
    ) -> BridgedResult:
        """Run PipesHub's own retrieval scoped to the accessible universe.

        No-op (returns *bridged* unchanged) when there's nothing to search
        or no retrieval service — callers should treat a `None`
        `retrieval_response` as "show the plain listing instead".
        """
        virtual_record_ids = [
            r.virtual_record_id for r in bridged.accessible_records if r.virtual_record_id
        ]
        response = await search_within_virtual_record_ids(
            self._retrieval, virtual_record_ids, content_query, user_id, org_id, limit=limit,
        )
        if response is not None:
            bridged.content_search_ran = True
            bridged.retrieval_response = response
        return bridged


async def search_within_virtual_record_ids(
    retrieval_service: "RetrievalService | None",
    virtual_record_ids: list[str],
    content_query: str,
    user_id: str,
    org_id: str,
    limit: int = 50,
) -> dict[str, Any] | None:
    """The actual `RetrievalService.search_with_filters(virtual_record_ids_
    from_tool=...)` call, factored out of `search_content_within` so the
    POST_TOOL_USE `filtered_retrieval` hook (`agent_loop/hooks/
    filtered_retrieval.py`) — which only has the already-gated
    `virtual_record_id`s from a tool's JSON response, not a `BridgedResult`
    — can reuse the exact same call instead of re-deriving it.

    Returns `None` (never raises) when there is nothing to search or the
    retrieval call itself fails, so every caller can treat `None` uniformly
    as "fall back to the plain listing".
    """
    if not virtual_record_ids or retrieval_service is None:
        return None
    try:
        return await retrieval_service.search_with_filters(
            queries=[content_query],
            user_id=user_id,
            org_id=org_id,
            limit=limit,
            virtual_record_ids_from_tool=virtual_record_ids,
        )
    except Exception:
        logger.exception("search_within_virtual_record_ids: content search failed")
        return None


__all__ = ["FilteredRetrievalBridge", "BridgedResult", "search_within_virtual_record_ids"]
