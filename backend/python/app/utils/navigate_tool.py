"""``navigate`` tool — the agent's "file explorer".

Open any node in the knowledge graph (or nothing, for the root "sidebar" of
connected apps) and get back one consistent markdown "page": breadcrumbs, a
header, paginated children, and related (``LINKED_TO``) records.

Built entirely on top of ``KnowledgeHubService`` — the same permission-first
service backing the Knowledge Hub browse UI — so this tool is automatically
at parity across the ArangoDB and Neo4j providers and never re-implements
permission-aware traversal.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.logger import create_logger
from app.utils.record_tool_helpers import (
    NodeRefMapper,
    check_record_access,
    register_tool_describer,
    render_node_view_markdown,
    truncate_name,
)

logger = create_logger(__name__)

FOLDER_MIME_TYPES = [
    "application/vnd.folder",
    "application/vnd.google-apps.folder",
    "text/directory",
]

MAX_LIMIT = 50
MAX_RELATED = 20
MAX_TREE_NODES = 100
TREE_CHILDREN_PER_NODE = 10


class NavigateArgs(BaseModel):
    """Args for the ``navigate`` tool."""

    node_id: str | None = Field(
        default=None,
        description=(
            "Node to open: a node ref like n3, a Record ID, Record Group ID, or Connector/App ID. "
            "Omit to see the root view (all connected apps)."
        ),
    )
    name_filter: str | None = Field(
        default=None,
        description=(
            "Filter children by name, like typing in a UI filter box. "
            "Use instead of paginating when looking for something specific."
        ),
    )
    depth: int = Field(
        default=1,
        ge=1,
        le=3,
        description=(
            "1 = flat listing (default). 2-3 = compact indented tree of descendants "
            "(names + counts only) — use to survey structure in one call."
        ),
    )
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=MAX_LIMIT)
    reason: str = Field(default="Navigating the knowledge graph")


def _describe_navigate(args: dict[str, Any]) -> str:
    node_id = args.get("node_id")
    page = args.get("page") or 1
    name_filter = args.get("name_filter")
    if name_filter:
        return f"Searching for \"{name_filter}\"…"
    if not node_id:
        return "Browsing connected apps…"
    suffix = f" (page {page})" if page and page > 1 else ""
    return f"Opening {node_id}{suffix}…"


register_tool_describer("navigate", _describe_navigate)


def _node_type_label(item: NodeItem) -> str:
    node_type = item.nodeType
    if node_type == NodeType.RECORD.value:
        return item.recordType or "RECORD"
    if node_type == NodeType.RECORD_GROUP.value:
        return item.recordGroupType or "GROUP"
    if node_type == NodeType.APP.value:
        return "APP"
    return "FOLDER"


def _format_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return ""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _item_view(item: NodeItem, ref_mapper: NodeRefMapper) -> dict[str, Any]:
    ref = ref_mapper.get_or_create_ref(item.id)
    detail_bits: list[str] = []
    if item.isPlaceholder:
        detail_bits.append("placeholder — not yet synced")
    elif item.nodeType == NodeType.RECORD.value and item.indexingStatus and item.indexingStatus != "COMPLETED":
        detail_bits.append(f"indexing: {item.indexingStatus}")
    size_str = _format_size(item.sizeInBytes)
    if size_str:
        detail_bits.append(size_str)
    if item.connector and str(item.origin) == "CONNECTOR":
        detail_bits.append(item.connector)
    return {
        "ref": ref,
        "id": item.id,
        "type": _node_type_label(item),
        "name": truncate_name(item.name),
        "detail": ", ".join(detail_bits),
        "expandable": bool(item.hasChildren),
    }


async def _get_related_records(
    graph_provider: IGraphDBProvider,
    org_id: str,
    user_id: str,
    record_id: str,
    ref_mapper: NodeRefMapper,
) -> list[dict[str, Any]]:
    try:
        related = await graph_provider.get_related_records_by_relation_type(
            record_id,
            RecordRelations.LINKED_TO.value,
            CollectionNames.RECORD_RELATIONS.value,
        )
    except Exception:
        logger.debug("navigate: related-records lookup failed for %s", record_id, exc_info=True)
        return []

    results: list[dict[str, Any]] = []
    for rel in (related or [])[:MAX_RELATED]:
        rel_id = rel.get("id") or rel.get("_key")
        if not rel_id:
            continue
        doc = await check_record_access(graph_provider, user_id, org_id, rel_id)
        if not doc:
            continue
        connector = doc.get("connectorName")
        results.append({
            "ref": ref_mapper.get_or_create_ref(rel_id),
            "id": rel_id,
            "type": doc.get("recordType") or "RECORD",
            "name": truncate_name(doc.get("recordName")),
            "detail": f"LINKED_TO, {connector}" if connector else "LINKED_TO",
        })
    return results


def _build_view(
    response: KnowledgeHubNodesResponse,
    ref_mapper: NodeRefMapper,
    related: list[dict[str, Any]],
    page: int,
    name_filter: str | None,
    record_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    current = None
    if response.currentNode:
        cn = response.currentNode
        current = {
            "ref": ref_mapper.get_or_create_ref(cn.id),
            "id": cn.id,
            "type": (cn.subType or cn.nodeType or "NODE").upper(),
            "name": truncate_name(cn.name),
        }
        if record_doc:
            current["webUrl"] = None if record_doc.get("hideWeburl") else record_doc.get("webUrl")
            current["status"] = record_doc.get("indexingStatus")
            current["connector"] = record_doc.get("connectorName")

    breadcrumb_path = None
    if response.breadcrumbs:
        breadcrumb_path = " › ".join(truncate_name(b.name, 40) for b in response.breadcrumbs)

    items = [_item_view(item, ref_mapper) for item in response.items]

    heading = f'Contents matching "{name_filter}"' if name_filter else "Contents"

    pagination = None
    if response.pagination:
        pagination = response.pagination.model_dump() if hasattr(response.pagination, "model_dump") else dict(response.pagination)

    return {
        "current": current,
        "breadcrumb_path": breadcrumb_path,
        "items": items,
        "items_heading": heading,
        "related": related,
        "pagination": pagination,
        "is_delta": page > 1,
    }


async def _navigate_tree(
    service: KnowledgeHubService,
    graph_provider: IGraphDBProvider,
    org_id: str,
    user_id: str,
    root_id: str | None,
    depth: int,
    ref_mapper: NodeRefMapper,
) -> dict[str, Any]:
    """Bounded BFS-ish traversal reusing get_nodes at each level — no new provider methods."""
    root_type: str | None = None
    if root_id:
        node_info = await graph_provider.get_knowledge_hub_node_info(root_id, FOLDER_MIME_TYPES)
        if not node_info:
            return {"ok": False, "error": "Node not found or not accessible."}
        root_type = node_info.get("nodeType")
        if root_type in (NodeType.RECORD.value, NodeType.FOLDER.value):
            doc = await check_record_access(graph_provider, user_id, org_id, root_id)
            if not doc:
                return {"ok": False, "error": "Node not found or not accessible."}

    lines: list[str] = []
    visited = 0
    truncated = False

    async def _walk(node_id: str | None, node_type: str | None, level: int) -> None:
        nonlocal visited, truncated
        if level > depth or visited >= MAX_TREE_NODES:
            return
        try:
            resp = await service.get_nodes(
                user_id=user_id,
                org_id=org_id,
                parent_id=node_id,
                parent_type=node_type,
                page=1,
                limit=TREE_CHILDREN_PER_NODE,
            )
        except Exception:
            logger.debug("navigate tree: get_nodes failed for %s", node_id, exc_info=True)
            return
        if not resp.success:
            return
        if resp.pagination and resp.pagination.totalItems > len(resp.items):
            truncated = True
        for item in resp.items:
            if visited >= MAX_TREE_NODES:
                truncated = True
                return
            visited += 1
            ref = ref_mapper.get_or_create_ref(item.id)
            indent = "  " * (level - 1)
            marker = " ▸" if item.hasChildren else ""
            lines.append(f"{indent}- {ref} [{_node_type_label(item)}] {truncate_name(item.name)}{marker}")
            if item.hasChildren and level < depth:
                await _walk(item.id, item.nodeType, level + 1)

    await _walk(root_id, root_type, 1)

    root_ref = ref_mapper.get_or_create_ref(root_id) if root_id else "root"
    header = f"# Tree view from {root_ref} (depth {depth})\n"
    body = "\n".join(lines) if lines else "(no children)"
    footer = "\n\n(truncated — descend with navigate(node_id=\"...\") for more)" if truncated else ""

    return {
        "ok": True,
        "content": [{"type": "text", "text": header + body + footer}],
        "result_type": "content",
        "navigation": {"node": {"id": root_id, "ref": root_ref}, "mode": "tree"},
        "summary": f"Surveyed tree from {root_ref} (depth {depth})",
    }


def create_navigate_tool(
    *,
    graph_provider: IGraphDBProvider | None,
    org_id: str | None,
    user_id: str | None,
    node_ref_mapper: NodeRefMapper | None = None,
) -> Callable:
    """Factory that creates the ``navigate`` tool with runtime dependencies injected."""
    logger.info(
        "[TOOL-REG] create_navigate_tool: org_id=%s user_id=%s graph_provider=%s",
        org_id, user_id, bool(graph_provider),
    )
    ref_mapper = node_ref_mapper if node_ref_mapper is not None else NodeRefMapper()
    service = (
        KnowledgeHubService(logger=logging.getLogger("navigate_tool"), graph_provider=graph_provider)
        if graph_provider
        else None
    )

    @tool("navigate", args_schema=NavigateArgs)
    async def navigate_tool(
        node_id: str | None = None,
        name_filter: str | None = None,
        depth: int = 1,
        page: int = 1,
        limit: int = 20,
        reason: str = "Navigating the knowledge graph",
    ) -> dict[str, Any]:
        """Open a node in the knowledge graph and see what's there — the "file explorer".

        Omit node_id to see the root "sidebar" of connected apps (Jira, Drive, Confluence, ...).
        Pass a node_id (a ref from a previous navigate/lookup_record call, or a raw Record/
        RecordGroup/App ID) to descend into it: apps show record groups, record groups show
        their records/folders, and records show their children (comments, attachments,
        sub-tasks) plus a "Related" section of cross-referenced records (e.g. a Confluence
        page linked from a Jira ticket).

        Typical trajectory, like a human clicking through a UI:
            navigate()                    -> sidebar: which apps exist
            navigate(node_id=<app>)        -> record groups inside that app
            navigate(node_id=<group>)      -> records in that group (page 1)
            navigate(node_id=<record>)     -> record header + children + related
            fetch_full_record(record_ids=[<id>])  -> read the actual content

        Use name_filter instead of paging through a large listing when you're looking for
        something specific — it searches within the current node's descendants in one call.
        Use depth=2 or 3 to get a compact tree survey of structure instead of one flat page.

        Args:
            node_id: Node to open (ref or ID). Omit for the root view.
            name_filter: Filter children by name instead of paginating.
            depth: 1 = flat listing (default). 2-3 = compact tree of descendants.
            page: Page number for the flat listing (ignored when depth > 1).
            limit: Items per page (max 50).
            reason: Brief explanation of why this navigation step is needed.

        Returns: {"ok": true, "content": [...]} markdown page, or {"ok": false, "error": "..."}.
        """
        logger.info(
            "navigate called: node_id=%r name_filter=%r depth=%s page=%s limit=%s reason=%r",
            node_id, name_filter, depth, page, limit, reason,
        )

        if not (service and org_id and user_id):
            return {"ok": False, "error": "Navigation is not available in this context."}

        resolved_node_id = ref_mapper.resolve(node_id) if node_id else None

        try:
            if depth > 1:
                return await _navigate_tree(service, graph_provider, org_id, user_id, resolved_node_id, depth, ref_mapper)

            parent_type: str | None = None
            record_doc: dict[str, Any] | None = None
            if resolved_node_id:
                node_info = await graph_provider.get_knowledge_hub_node_info(resolved_node_id, FOLDER_MIME_TYPES)
                if not node_info:
                    return {"ok": False, "error": "Node not found or not accessible."}
                parent_type = node_info.get("nodeType")
                if parent_type in (NodeType.RECORD.value, NodeType.FOLDER.value):
                    # Knowledge Hub children queries are permission-filtered, but the
                    # current-node header itself is not — gate it explicitly so an
                    # inaccessible record/folder's name/type can't leak through the header.
                    # Folders are records too (a record with a folder mimeType), so both
                    # node types resolve through the same records collection here.
                    record_doc = await check_record_access(graph_provider, user_id, org_id, resolved_node_id)
                    if not record_doc:
                        return {"ok": False, "error": "Node not found or not accessible."}

            include = ["breadcrumbs"] if page == 1 else []
            response = await service.get_nodes(
                user_id=user_id,
                org_id=org_id,
                parent_id=resolved_node_id,
                parent_type=parent_type,
                page=page,
                limit=limit,
                q=name_filter,
                include=include,
            )
        except Exception as e:
            logger.exception("navigate: get_nodes failed for node_id=%s", resolved_node_id)
            return {"ok": False, "error": f"Navigation failed: {e}"}

        if not response.success:
            return {"ok": False, "error": response.error or "Navigation failed."}

        related: list[dict[str, Any]] = []
        if resolved_node_id and parent_type == NodeType.RECORD.value and page == 1:
            related = await _get_related_records(graph_provider, org_id, user_id, resolved_node_id, ref_mapper)

        view = _build_view(response, ref_mapper, related, page, name_filter, record_doc)
        markdown = render_node_view_markdown(view)

        total_items = response.pagination.totalItems if response.pagination else len(response.items)
        return {
            "ok": True,
            "content": [{"type": "text", "text": markdown}],
            "result_type": "content",
            "navigation": {
                "breadcrumbs": [b.model_dump() for b in (response.breadcrumbs or [])] if response.breadcrumbs else [],
                "node": view.get("current"),
                "page": page,
                "totalItems": total_items,
            },
            "summary": f"{len(response.items)} of {total_items} items" if response.items else "No items found",
        }

    return navigate_tool
