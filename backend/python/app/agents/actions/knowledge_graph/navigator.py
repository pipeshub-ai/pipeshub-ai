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
# Matches KnowledgeHubService.get_nodes()'s own cap (200) minus headroom —
# kept below it rather than equal so a depth>=2 expansion's per-parent
# sub-fetches (see `_depth_limit`) still shrink from a real ceiling instead
# of already being at the backend's hard limit.
_MAX_LIMIT = 100
_MAX_DEPTH = 3
# Bounds the total number of extra `get_record_by_id` reads a single
# depth>=2 navigate() call can trigger for condensed per-row metadata —
# a wide depth=3 hierarchy must not turn into dozens of uncapped record
# reads. See `_attach_context_summaries`.
_MAX_DEPTH_CONTEXT_SUMMARIES = 30


def _depth_limit(base_limit: int, level: int) -> int:
    """Per-parent children cap at nesting `level` (2 or 3) — shrinks as
    depth increases so `limit * depth_limit(2) [* depth_limit(3)]` stays
    bounded even when every row at every level has children. Level 1 (the
    top listing) keeps the caller's own `limit` untouched."""
    if level == 2:
        return max(5, base_limit // 2)
    if level == 3:
        return max(3, base_limit // 4)
    return base_limit


def _flatten_rows(rows: list[NodeRow]) -> list[NodeRow]:
    """All rows across every nested level already fetched — depth-first,
    parents before their own children."""
    out: list[NodeRow] = []
    for row in rows:
        out.append(row)
        if row.children:
            out.extend(_flatten_rows(row.children))
    return out


def _condensed_summary(context_block: str) -> str | None:
    """Extract the `* Field: value` lines from a `Record.to_llm_context()`
    block into one comma-joined line — the type-specific metadata (status,
    assignee, priority, ...) a deep row needs, without repeating the
    identity fields (id/name/url) the row already prints itself."""
    parts = [
        line[2:].strip()
        for line in context_block.splitlines()
        if line.startswith("* ")
    ]
    return ", ".join(parts) if parts else None


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

    # `createdAt`/`updatedAt` on NodeItem are already the projected source
    # timestamps (KnowledgeHubService._doc_to_node_item / the graph
    # provider's coalescing expressions prefer sourceCreatedAtTimestamp for
    # connector records). 0 is the "missing" sentinel used when a doc has no
    # timestamp at all — treat it as None so we don't render 1970-01-01.
    created_at = getattr(item, "createdAt", None) or None
    updated_at = getattr(item, "updatedAt", None) or None

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
        source_created_at=created_at,
        source_modified_at=updated_at,
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

    async def _expand_row_children(
        self,
        row: NodeRow,
        *,
        limit: int,
        connector_ids: list[str] | None,
        record_group_ids: list[str] | None,
        created_at: dict[str, int | None] | None = None,
        updated_at: dict[str, int | None] | None = None,
    ) -> None:
        """Fetch `row`'s own direct children in place, mutating `row.children`
        — the same browse path `navigate()` itself uses for the top-level
        listing (`KnowledgeHubService.get_nodes`), just scoped to this row
        instead of the node the caller asked for. A failure here degrades to
        "no children shown for this row" rather than failing the whole call.

        `created_at`/`updated_at` are the same time-range filters applied to
        the top-level listing — propagated here so a time-filtered navigate()
        with depth >= 2 doesn't only filter the first level while showing
        every nested child unfiltered."""
        if not row.has_children:
            return
        try:
            response = await self._service.get_nodes(
                user_id=self._user_id,
                org_id=self._org_id,
                parent_id=row.id,
                parent_type=row.node_type,
                page=1,
                limit=limit,
                connector_ids=connector_ids,
                record_group_ids=record_group_ids,
                created_at=created_at,
                updated_at=updated_at,
            )
        except Exception as e:
            logger.warning("navigate depth expansion failed for node %s: %s", row.id, e)
            return
        sub_rows = [_node_item_to_row(item) for item in (response.items or [])]
        row.children = sub_rows
        pag = response.pagination
        total = pag.totalItems if pag else len(sub_rows)
        row.children_total = total
        row.children_truncated = total > len(sub_rows)

    async def _expand_depth(
        self,
        rows: list[NodeRow],
        *,
        depth: int,
        limit: int,
        connector_ids: list[str] | None,
        record_group_ids: list[str] | None,
        created_at: dict[str, int | None] | None = None,
        updated_at: dict[str, int | None] | None = None,
    ) -> None:
        """Fetch and attach nested children onto `rows` in place, up to
        `depth` levels total (`rows` itself is level 1; this method adds
        level 2 and, when `depth == 3`, level 3). Each level's fetches run
        concurrently via `asyncio.gather` — only rows with `has_children`
        cost a query — and use a shrinking per-parent limit (see
        `_depth_limit`) so a wide hierarchy can't blow up the response.

        `created_at`/`updated_at` (see `_expand_row_children`) are forwarded
        to every level so a time-filtered call stays time-filtered at every
        depth, not just the top one."""
        if depth < 2:
            return

        level2_limit = _depth_limit(limit, level=2)
        expandable = [r for r in rows if r.has_children]
        if not expandable:
            return
        await asyncio.gather(*(
            self._expand_row_children(
                row, limit=level2_limit,
                connector_ids=connector_ids, record_group_ids=record_group_ids,
                created_at=created_at, updated_at=updated_at,
            )
            for row in expandable
        ))

        if depth < 3:
            return

        level3_limit = _depth_limit(limit, level=3)
        level2_expandable = [
            sub
            for row in expandable if row.children
            for sub in row.children if sub.has_children
        ]
        if not level2_expandable:
            return
        await asyncio.gather(*(
            self._expand_row_children(
                sub, limit=level3_limit,
                connector_ids=connector_ids, record_group_ids=record_group_ids,
                created_at=created_at, updated_at=updated_at,
            )
            for sub in level2_expandable
        ))

    async def _attach_context_summaries(self, rows: list[NodeRow]) -> None:
        """Condensed one-line metadata (status/assignee/priority/...) for
        every record/folder row shown across all fetched levels — the
        type-specific `* ` lines `Record.to_llm_context()` would print,
        minus the identity fields the row already carries. Only called for
        depth >= 2: at depth=1, the *current* node already gets the full
        block via `_context_block`, and adding a record read per child row
        would regress depth=1's existing cost profile for no new output.
        Capped at `_MAX_DEPTH_CONTEXT_SUMMARIES` total reads regardless of
        how many rows a depth=3 hierarchy surfaces."""
        candidates = [r for r in _flatten_rows(rows) if r.is_record][:_MAX_DEPTH_CONTEXT_SUMMARIES]
        if not candidates:
            return
        records = await asyncio.gather(
            *(self._graph.get_record_by_id(record_id=r.id) for r in candidates),
            return_exceptions=True,
        )
        for row, record in zip(candidates, records):
            if isinstance(record, BaseException) or record is None:
                continue
            try:
                block = record.to_llm_context(frontend_url=self._frontend_url)
            except Exception:
                continue
            if isinstance(block, str):
                row.context_summary = _condensed_summary(block)

    async def navigate(
        self,
        node_id: str | None = None,
        name_filter: str | None = None,
        page: int = 1,
        limit: int = 50,
        connector_ids: list[str] | None = None,
        record_group_ids: list[str] | None = None,
        depth: int = 1,
        created_at: dict[str, int | None] | None = None,
        updated_at: dict[str, int | None] | None = None,
    ) -> NavigationView:
        """Build a NavigationView for the given node.

        node_id=None → root (list of connected apps).
        depth > 1 additionally fetches and inlines 1-2 more levels of
        children (see `_expand_depth`) so the model can see a hierarchy
        overview (e.g. an epic's stories AND their subtasks) in one call
        instead of walking down one navigate() per level. Restricted to
        page 1 — same rationale as breadcrumbs/related below: a paginated
        deep listing would multiply the already-bounded query fan-out by
        every page the model asks for.

        `created_at`/`updated_at` are optional `{"gte": epoch_ms|None,
        "lte": epoch_ms|None}` filters on the child's source
        creation/modification timestamp — passed straight through to
        `KnowledgeHubService.get_nodes()`, whose `_has_flattening_filters`
        already treats a non-empty dict here as a signal to use the
        search/flattened path (which supports these filters) instead of
        the plain browse path. Applied at every depth level fetched (see
        `_expand_depth`), not just the top listing.
        """
        limit = min(max(1, limit), _MAX_LIMIT)
        page = max(1, page)
        depth = min(max(1, depth), _MAX_DEPTH)

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
            created_at=created_at,
            updated_at=updated_at,
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

        # ── Depth expansion (page 1 only, depth >= 2) ───────────────────
        if page == 1 and depth >= 2 and rows:
            await self._expand_depth(
                rows, depth=depth, limit=limit,
                connector_ids=connector_ids, record_group_ids=record_group_ids,
                created_at=created_at, updated_at=updated_at,
            )
            await self._attach_context_summaries(rows)

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
