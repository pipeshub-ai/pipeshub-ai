"""GraphNavigator: node → NavigationView.

Wraps KnowledgeHubService (for children + breadcrumbs) and IGraphDBProvider
(for node-access gate and linked records). No FastAPI coupling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config.constants.arangodb import RecordRelations
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    FOLDER_MIME_TYPES,
    KnowledgeHubService,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

from .models import NavigationView, NodeRef, NodeRow, PaginationInfo

logger = logging.getLogger(__name__)

# All RecordRelations that represent containment (excluded from "Related" section)
_CONTAINMENT_RELATIONS = frozenset({
    RecordRelations.PARENT_CHILD.value,
    RecordRelations.ATTACHMENT.value,
})

# All cross-reference relation types (everything else)
_CROSS_REF_RELATIONS = [
    r.value for r in RecordRelations if r.value not in _CONTAINMENT_RELATIONS
]

_MAX_RELATED = 10
_MAX_LIMIT = 50


def _node_ref(
    node_id: str,
    name: str,
    node_type: str,
    sub_type: str | None = None,
) -> NodeRef:
    return NodeRef(
        id=node_id,
        name=name,
        node_type=node_type,
        sub_type=sub_type,
        is_record=node_type in ("record", "folder"),
    )


def _node_item_to_row(item: Any) -> NodeRow:
    """Convert a NodeItem (from KnowledgeHubService) to a NodeRow."""
    node_type = getattr(item, "nodeType", None)
    if hasattr(node_type, "value"):
        node_type = node_type.value
    sub_type = (
        getattr(item, "recordType", None)
        or getattr(item, "recordGroupType", None)
        or getattr(item, "connector", None)
    )

    detail_parts = []
    size = getattr(item, "sizeInBytes", None)
    if size:
        detail_parts.append(f"{size // 1024} KB" if size >= 1024 else f"{size} B")
    status = getattr(item, "indexingStatus", None)
    if status and status != "COMPLETED":
        detail_parts.append(status)

    return NodeRow(
        id=item.id,
        name=item.name,
        node_type=node_type or "record",
        sub_type=sub_type,
        is_record=node_type in ("record", "folder"),
        has_children=getattr(item, "hasChildren", False),
        detail=", ".join(detail_parts) or None,
        web_url=getattr(item, "webUrl", None),
        indexing_status=status,
    )


def _linked_dict_to_row(d: dict[str, Any]) -> NodeRow:
    """Convert a get_linked_records dict to a NodeRow."""
    return NodeRow(
        id=d.get("id", ""),
        name=d.get("name", ""),
        node_type="record",
        sub_type=d.get("recordType"),
        is_record=True,
        has_children=d.get("hasChildren", False),
        detail=d.get("relationshipType"),
        web_url=d.get("webUrl"),
        indexing_status=d.get("indexingStatus"),
    )


class GraphNavigator:
    """Navigate from a node_id to a NavigationView.

    Depends only on:
    - KnowledgeHubService (stateless, constructed per call)
    - IGraphDBProvider (for get_knowledge_hub_node_access + get_linked_records)
    """

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        user_id: str,
        user_key: str,
        org_id: str,
        frontend_url: str | None = None,
    ) -> None:
        self._graph = graph_provider
        self._user_id = user_id
        self._user_key = user_key
        self._org_id = org_id
        self._frontend_url = frontend_url
        self._service = KnowledgeHubService(
            logger=logger,
            graph_provider=graph_provider,
        )

    async def _context_block(self, node_id: str) -> str | None:
        """`Record.to_llm_context()` for an already-authorized record node.

        `get_knowledge_hub_node_access` returns identity and indexing state
        only, so without this a ticket node shows no status, assignee or
        priority — the fields a question about it usually turns on. Degrades
        to None rather than failing navigation.

        Call only AFTER the node-access gate has passed: this read is not
        itself permission-filtered.
        """
        try:
            record = await self._graph.get_record_by_id(record_id=node_id)
        except Exception as e:
            logger.warning("get_record_by_id failed for %s: %s", node_id, e)
            return None
        if record is None:
            return None
        try:
            block = record.to_llm_context(frontend_url=self._frontend_url)
        except Exception as e:
            logger.warning("to_llm_context failed for %s: %s", node_id, e)
            return None
        # A provider that hands back something other than a typed Record
        # must not take the whole navigation down with it.
        return block if isinstance(block, str) else None

    async def navigate(
        self,
        node_id: str | None = None,
        name_filter: str | None = None,
        page: int = 1,
        limit: int = 20,
        connector_ids: list[str] | None = None,
        record_group_ids: list[str] | None = None,
    ) -> NavigationView:
        """Build a NavigationView for the given node.

        node_id=None → root (list of connected apps).
        """
        limit = min(max(1, limit), _MAX_LIMIT)
        page = max(1, page)

        current: NodeRef | None = None
        breadcrumbs: list[NodeRef] = []
        web_url: str | None = None
        indexing_status: str | None = None
        connector: str | None = None
        related: list[NodeRow] = []
        context_block: str | None = None

        # ── Resolve the node being opened ──────────────────────────────
        parent_type: str | None = None

        if node_id:
            node_info = await self._graph.get_knowledge_hub_node_access(
                node_id=node_id,
                user_key=self._user_key,
                org_id=self._org_id,
                folder_mime_types=FOLDER_MIME_TYPES,
            )
            if node_info is None:
                # Missing or denied — return empty navigation (no info leak)
                return NavigationView(
                    current=None,
                    breadcrumbs=[],
                    rows=[],
                    related=[],
                    pagination=None,
                    web_url=None,
                    indexing_status=None,
                    connector=None,
                )

            parent_type = node_info["nodeType"]
            current = _node_ref(
                node_id,
                node_info["name"],
                node_info["nodeType"],
                node_info.get("subType"),
            )
            web_url = node_info.get("webUrl")
            indexing_status = node_info.get("indexingStatus")
            connector = node_info.get("connector")

            # Breadcrumbs and record metadata only on page 1 to save tokens.
            # Gathered so the extra record read costs no extra latency.
            if page == 1:
                crumbs = self._graph.get_knowledge_hub_breadcrumbs(node_id=node_id)
                if parent_type in ("record", "folder"):
                    raw_crumbs, context_block = await asyncio.gather(
                        crumbs, self._context_block(node_id)
                    )
                else:
                    raw_crumbs = await crumbs
                breadcrumbs = [
                    _node_ref(
                        bc.get("id", ""),
                        bc.get("name", ""),
                        bc.get("nodeType", ""),
                        bc.get("subType"),
                    )
                    for bc in (raw_crumbs or [])
                    if bc.get("id") and bc.get("id") != node_id
                ]

        # ── Fetch children ─────────────────────────────────────────────
        response = await self._service.get_nodes(
            user_id=self._user_id,
            org_id=self._org_id,
            parent_id=node_id,
            parent_type=parent_type,
            page=page,
            limit=limit,
            q=name_filter if name_filter else None,
            connector_ids=connector_ids,
            record_group_ids=record_group_ids,
            # page 1 only: include breadcrumbs is handled above via get_knowledge_hub_breadcrumbs
        )

        rows: list[NodeRow] = [_node_item_to_row(item) for item in (response.items or [])]

        pag_raw = response.pagination
        pagination: PaginationInfo | None = None
        if pag_raw:
            pagination = PaginationInfo(
                page=pag_raw.page,
                limit=pag_raw.limit,
                total=pag_raw.totalItems,
                has_next=pag_raw.hasNext,
                has_prev=pag_raw.hasPrev,
            )

        # ── Linked records (page 1, record/folder nodes only) ──────────
        if page == 1 and node_id and parent_type in ("record", "folder"):
            try:
                linked = await self._graph.get_linked_records(
                    record_id=node_id,
                    org_id=self._org_id,
                    user_key=self._user_key,
                    relation_types=_CROSS_REF_RELATIONS,
                    limit=_MAX_RELATED,
                )
                related = [_linked_dict_to_row(d) for d in linked if d.get("id")]
            except Exception as e:
                logger.warning("get_linked_records failed for %s: %s", node_id, e)

        return NavigationView(
            current=current,
            breadcrumbs=breadcrumbs,
            rows=rows,
            related=related,
            pagination=pagination,
            web_url=web_url,
            indexing_status=indexing_status,
            connector=connector,
            context_block=context_block,
        )
