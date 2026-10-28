"""Knowledge graph listing operation.

Extracted from KnowledgeHub.list_files and updated to:
- Accept ``source_ids`` instead of the split ``connector_ids`` / ``record_group_ids``
- Return flat-text output (same grammar as navigate) instead of JSON
- Drop the four unreachable parameters: parent_id, parent_type, only_containers, flattened

The underlying KnowledgeHubService call is unchanged; only the LLM surface
is simplified.
"""
from __future__ import annotations

import json
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.listing_citations import cite_listed_records
from app.agents.actions.knowledge_graph.views import CITE_NOTE
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
)
from app.connectors.sources.localKB.handlers.kh_bounded import (
    BOUNDED_BATCH,
    BOUNDED_MAX_BATCHES,
    Position,
    read_inside,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.modules.demo_data.chat import excluded_app_ids
from app.services.graph_db.interface.graph_db_provider import ALLOWED_FILTER_KEYS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from app.modules.agents.qna.chat_state import ChatState
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

_VALID_NODE_TYPES = {nt.value for nt in NodeType}
_VALID_SORT_FIELDS = {"name", "createdAt", "updatedAt", "size", "type"}
_VALID_SORT_ORDERS = {"asc", "desc"}

_FETCHABLE_NODE_TYPES = frozenset({NodeType.RECORD.value, NodeType.FOLDER.value})

MIN_QUERY_LENGTH = 2

_NO_NAMED_SOURCE_NOTE = (
    "None of the named sources is available in this conversation; "
    "these are the sources you can list."
)
_NAME_A_SOURCE_NOTE = (
    "To list records or folders, name one source: call again with "
    "source_ids=[<node_id of a source below>]."
)
_TOO_LARGE_MESSAGE = (
    "This listing is too large to read at once. Name one source with source_ids, "
    "open a folder with knowledgegraph__navigate(), or pass a query."
)
MAX_QUERY_LENGTH = 500

_NODE_TYPE_LABELS = {
    NodeType.APP.value: "App",
    NodeType.RECORD_GROUP.value: "RecordGroup",
    NodeType.FOLDER.value: "Folder",
    NodeType.RECORD.value: "Record",
}


def _record_ids_in_items(items: list[NodeItem] | None) -> list[str]:
    ids: list[str] = []
    for item in items or []:
        node_type = getattr(item.nodeType, "value", item.nodeType)
        if node_type in _FETCHABLE_NODE_TYPES and item.id:
            ids.append(item.id)
    return ids


def _normalize_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else None
    if isinstance(value, list):
        filtered = [str(v) for v in value if v]
        return filtered if filtered else None
    return None


def _next_page_hint(page: int) -> str:
    return f"(More items follow: call again with page={page + 1}.)"


def _render_flat_text(
    response: KnowledgeHubNodesResponse,
    query: str | None,
    shortener: "RecordIdShortener | None" = None,
    cite_refs: "Mapping[str, str] | None" = None,
    *,
    page: int = 1,
) -> str:
    """Render list_files output in the same flat-text grammar as navigate().

    `shortener` is the TEMPORARY token-savings `RecordIdShortener` (see
    `app.utils.chat_helpers`) — when supplied, every printed id becomes a
    short `R<n>` label instead of the full record id. `cite_refs` (Record ID ->
    Citation ID) marks the records an answer may cite for their names.
    """
    cite_refs = cite_refs or {}
    if not response.success:
        if getattr(response, "errorCode", None) == HTTPStatus.UNPROCESSABLE_ENTITY:
            return f"Error: {_TOO_LARGE_MESSAGE}"
        error = response.error or "Failed to browse knowledge files"
        return f"Error: {error}"

    items = response.items or []
    if not items:
        if query:
            return f'No items found matching "{query}".'
        return "No items found."

    lines: list[str] = []
    header_parts: list[str] = [f"Found {len(items)} item{'s' if len(items) != 1 else ''}"]
    if query:
        header_parts.append(f'matching "{query}"')
    lines.append(" ".join(header_parts) + ".")
    cited = [cite_refs[item.id] for item in items if item.id in cite_refs]
    if cited:
        lines.append(CITE_NOTE.format(ref=cited[0]))
    lines.append("")

    for item in items:
        node_type = getattr(item.nodeType, "value", str(item.nodeType))
        label = _NODE_TYPE_LABELS.get(node_type, node_type)
        sub = getattr(item, "subType", None) or ""
        type_str = f"{label}/{sub}" if sub else label

        id_label = "record_id" if node_type in _FETCHABLE_NODE_TYPES else "node_id"
        item_id = shortener.get_or_create_short_id(item.id) if shortener is not None else item.id
        line = f"  [{type_str}] {item.name}  | {id_label}={item_id}"
        if item.id in cite_refs:
            line += f"  | cite={cite_refs[item.id]}"
        if hasattr(item, "webUrl") and item.webUrl:
            line += f"  | url={item.webUrl}"
        lines.append(line)

    lines.append("")
    total = getattr(response, "total", None)
    if total and total > len(items):
        remaining = total - len(items)
        lines.append(f"({remaining} more items — increase page or limit to see them)")
        lines.append("")
    elif getattr(getattr(response, "pagination", None), "hasNext", False):
        lines.append(_next_page_hint(page))
        lines.append("")

    lines.append(
        "Next: pass record_id to knowledgegraph__navigate() to see its children, "
        "or to knowledgegraph__fetch_record() to read its content."
    )
    return "\n".join(lines)


def _bound_as_selection(filters: dict[str, Any]) -> dict[str, Any]:
    """``filters`` with what every bound below app level names written as the
    turn's selection, so ``list_selected_nodes`` lists those nodes and keeps
    each one every bound admits: a file one bound names inside a folder another
    names is listed, and the folder is not. A bound of whole apps only adds
    nothing, or it would list every app it names."""
    from app.modules.retrieval.selection_scope import allowed_filters

    selection: dict[str, list[str]] = {"apps": [], "recordGroups": [], "records": []}
    for bound in allowed_filters(filters):
        if bound["recordGroups"] or bound["records"]:
            for key, ids in selection.items():
                ids.extend(i for i in bound[key] if i not in ids)
    bounded = {key: filters[key] for key in ALLOWED_FILTER_KEYS if key in filters}
    return {**bounded, **selection, "kb": []}


async def _page_inside_bounds(
    fetch: Callable[..., Awaitable[KnowledgeHubNodesResponse]],
    inside: Callable[[list[str]], Awaitable[set[str]]],
    *,
    page: int,
    limit: int,
) -> tuple[KnowledgeHubNodesResponse, str | None]:
    """Page ``page`` of the rows ``inside`` admits, read from the source's pages
    until it is full or they run out, and a line saying whether more follow.
    The response carries no total: the source's counts include dropped rows."""
    read = await read_inside(fetch, inside, start=Position(), want=page * limit + 1)
    if read.failed:
        return read.first, None
    items = read.kept[(page - 1) * limit:page * limit]
    if len(read.kept) > page * limit:
        hint = _next_page_hint(page)
    elif read.stop is not None:
        hint = (
            f"(Stopped after checking {BOUNDED_BATCH * BOUNDED_MAX_BATCHES} items; more may match. "
            "Narrow the query to find them.)"
        )
    else:
        hint = None
    return KnowledgeHubNodesResponse(success=True, items=items), hint


def _asks_for_contents(node_types: list[str] | None, record_types: list[str] | None) -> bool:
    """Whether a call asks for records or folders, which the root never holds."""
    return bool(_FETCHABLE_NODE_TYPES.intersection(node_types or ()) or record_types)


def _wants_flat_listing(
    query: str | None,
    matched_source_ids: list[str],
    *,
    wants_contents: bool,
    single_source: bool,
) -> bool | None:
    """The service's ``flattened`` flag: None searches, False lists the root
    (the sources themselves), True lists what is inside the sources.

    Without a query, the contents are listed for a source the call named, or
    for the only source in scope when the call asks for records or folders;
    across several sources the root is listed, so a listing never mixes them.
    """
    if query:
        return None
    return bool(matched_source_ids or (wants_contents and single_source))


async def execute_list_files(
    state: "ChatState",
    *,
    query: str | None = None,
    source_ids: list[str] | None = None,
    node_types: list[str] | None = None,
    record_types: list[str] | None = None,
    page: int = 1,
    limit: int = 20,
    sort_by: str = "updatedAt",
    sort_order: str = "desc",
    cursor: str | None = None,
) -> tuple[bool, str]:
    """List or search items in the knowledge graph.

    ``source_ids`` accepts both app-connector IDs and KB IDs; a KB is an app,
    so both filter by connector id.

    Returns ``(success, text)`` in the flat-text grammar.
    """
    if not state:
        return False, "Tool state not initialized."

    try:
        logger_instance = state.get("logger", logger)
        graph_provider = state.get("graph_provider")
        org_id = state.get("org_id", "")
        user_id = state.get("user_id", "")

        if not graph_provider:
            return False, "Graph provider not available."

        from app.agents.actions.knowledge_graph.ops.scope import (
            browsable_app_ids,
            ids_within_scope,
            list_selected_nodes,
            resolve_scope,
            turn_picked_sources,
        )
        from app.modules.retrieval.selection_scope import resolve_request_scopes

        selected = await list_selected_nodes(
            state, graph_provider, user_id, org_id, cursor=cursor,
        )
        if selected is not None:
            return True, selected

        scope = await resolve_scope(state)
        agent_source_ids = browsable_app_ids(state, scope)
        if not agent_source_ids:
            if turn_picked_sources(state):
                return True, "This conversation has no sources to list; only attached files."
            if not state.get("has_knowledge"):
                return False, "No knowledge sources configured for this agent."

        # Input normalization
        query = query.strip() if query else None
        if query and len(query) < MIN_QUERY_LENGTH:
            query = None
        elif query and len(query) > MAX_QUERY_LENGTH:
            query = query[:MAX_QUERY_LENGTH]

        source_ids_norm = _normalize_list(source_ids)
        node_types = _normalize_list(node_types)
        record_types = _normalize_list(record_types)

        if node_types:
            node_types = [nt for nt in node_types if nt in _VALID_NODE_TYPES] or None

        page = max(1, page)
        limit = min(max(1, limit), 50)
        if sort_by not in _VALID_SORT_FIELDS:
            sort_by = "updatedAt"
        if sort_order not in _VALID_SORT_ORDERS:
            sort_order = "desc"

        # source_ids may name apps and KBs; narrow to them, never widen. With no
        # agent sources the user picked nothing, and the service gates by the
        # user's own access: what they name is checked there.
        allowed = set(agent_source_ids)
        matched = [sid for sid in source_ids_norm or [] if not allowed or sid in allowed]
        note: str | None = None
        if source_ids_norm and not matched:
            # Listing the rest under a source the call named would pass it off
            # as that source's contents.
            query = None
            note = _NO_NAMED_SOURCE_NOTE
        use_connector_ids = matched or agent_source_ids

        # The agent's sources can be everything the user reads, while a bound
        # (a connected app's limit, a saved agent's or a project's sources) is
        # narrower, and the listing service knows nothing of it.
        filters = state.get("filters") or {}
        bound_user_key: str | None = None
        partial_ids: set[str] = set()
        if any(key in filters for key in ALLOWED_FILTER_KEYS):
            user = await graph_provider.get_user_by_user_id(user_id=user_id)
            bound_user_key = (user.get("_key") or user.get("id")) if user else None
            if not bound_user_key:
                return False, "User not found."
            _, bounds = await resolve_request_scopes(graph_provider, bound_user_key, org_id, filters)
            if not use_connector_ids and bounds:
                use_connector_ids = sorted(bounds[0].connector_ids)
            for bound in bounds:
                use_connector_ids = [sid for sid in use_connector_ids if sid in bound.connector_ids]
            if not use_connector_ids:
                return True, _render_flat_text(KnowledgeHubNodesResponse(success=True, items=[]), query)
            # Sources a bound reaches only through some of its folders or files.
            partial_ids = {sid for sid in use_connector_ids if any(sid not in b.app_ids for b in bounds)}
            if partial_ids and not query:
                # A root listing names sources, and a partly shared one lies
                # outside its own bound: list what the bound names instead.
                listing = await list_selected_nodes(
                    {**state, "filters": _bound_as_selection(filters)}, graph_provider, user_id, org_id,
                    cursor=cursor,
                )
                if listing is not None:
                    return True, listing
        wants_contents = _asks_for_contents(node_types, record_types)
        single_source = len(use_connector_ids) == 1 and note is None
        flattened = _wants_flat_listing(
            query, matched, wants_contents=wants_contents, single_source=single_source,
        )
        if flattened is False and wants_contents:
            # The root holds only sources: listed by record type it is empty.
            node_types = record_types = None
            note = note or _NAME_A_SOURCE_NOTE

        service = KnowledgeHubService(
            logger=logger_instance,
            graph_provider=graph_provider,
            excluded_app_ids=excluded_app_ids(state),
        )

        async def fetch(service_page: int, service_limit: int) -> KnowledgeHubNodesResponse:
            return await service.get_nodes(
                user_id=user_id,
                org_id=org_id,
                parent_id=None,
                parent_type=None,
                only_containers=False,
                page=service_page,
                limit=service_limit,
                sort_by=sort_by,
                sort_order=sort_order,
                q=query,
                node_types=node_types,
                record_types=record_types,
                connector_ids=use_connector_ids or None,
                flattened=flattened,
            )

        hint: str | None = None
        if bound_user_key and partial_ids:
            # This service hands out no cursors, so a batch is read by page number.
            response, hint = await _page_inside_bounds(
                lambda position, **_: fetch(position.page, BOUNDED_BATCH),
                lambda ids: ids_within_scope(state, graph_provider, bound_user_key, org_id, ids),
                page=page, limit=limit,
            )
        else:
            response = await fetch(page, limit)

        from app.modules.agents.qna.chat_state import remember_record_ids
        fetchable = _record_ids_in_items(response.items)
        remember_record_ids(
            state,
            fetchable,
            names={item.id: item.name for item in response.items or []},
        )
        # The listing states only names, so that is all a citation to one quotes.
        cite_refs = await cite_listed_records(state, graph_provider, org_id, dict.fromkeys(fetchable, ""))

        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        record_id_shortener = get_record_id_shortener_if_enabled(state)

        text = _render_flat_text(response, query, record_id_shortener, cite_refs, page=page)
        return True, "\n".join(part for part in (note, text, hint) if part)

    except Exception as exc:
        logger_instance = state.get("logger", logger) if state else logger
        logger_instance.error("knowledgegraph__list_files error: %s", exc, exc_info=True)
        return False, json.dumps({"status": "error", "message": f"Listing error: {exc}"})
