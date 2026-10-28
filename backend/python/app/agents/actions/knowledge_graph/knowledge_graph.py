"""KnowledgeGraph internal toolset — navigate and lookup_record.

Two tools that give the agent the same navigational view a human has:
  navigate      — walk App → RecordGroup → Record → Child (one call, one output shape).
  lookup_record — resolve a URL / issue key / external id to a Record ID.

Scoping for navigate:
  - agent's configured knowledge sources (connector_ids from agent_knowledge / filters)
  - falls back to org-wide user-accessible catalog when scope is empty (chat modes)

Scoping for lookup_record:
  - org-wide user-accessible catalog (so a Jira key found in a Confluence doc
    resolves even when the session is filtered to Confluence).
  - agent-scoped connectors are tried first within each strategy.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from app.modules.demo_data.chat import excluded_app_ids
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.entity_search_tool import (
    entity_search_offered_in,
    named_entities_enabled_in,
)
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.modules.agents.qna.chat_state import (
    ChatState,
    remember_record_ids,
)
from app.modules.named_entities.linking.mentions import MENTION_KIND_HINTS
from app.modules.retrieval.selection_scope import connection_sources_of
from app.utils.chat_helpers import (
    keep_context_blocks_inside_connection,
    keep_row_summaries_inside_connection,
    resolve_frontend_url,
)

from .catalog import ConnectorCatalog
from .models import NavigationView
from .navigator import GraphNavigator
from .ops.results import result_text, search_result_summary
from .ops.scope import (
    _has_selection,
    browsable_app_ids,
    ids_within_scope,
    navigation_nameable_ids,
    resolve_scope,
    selection_browse_refusal,
    turn_picked_sources,
)
from .ops.time_range import time_range_to_kh_filters
from .resolver import RecordResolver
from .views import render_lookup_result_with_ids, render_navigation_view_with_ids

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part
    from app.agent_loop_lib.core.types import ToolResult
    from app.agent_loop_lib.tools.base import Tool

logger = logging.getLogger(__name__)

_NOT_FOUND_MSG = "Not found or no access."


# ---------------------------------------------------------------------------
# Activity-summary helpers (colocated per project convention)
# ---------------------------------------------------------------------------


def _navigate_args_summary(args: dict[str, Any]) -> str | None:
    node_id = args.get("node_id")
    page = args.get("page", 1)
    depth = args.get("depth", 1)
    if node_id:
        base = f"Navigated to node {node_id}"
    else:
        base = "Navigated to root"
    if page and page > 1:
        base += f" — page {page}"
    if depth and depth > 1:
        base += f" (depth {depth})"
    time_bits = []
    if args.get("created_after") or args.get("created_before"):
        time_bits.append("created")
    if args.get("modified_after") or args.get("modified_before"):
        time_bits.append("modified")
    if time_bits:
        base += f" (time-filtered: {'/'.join(time_bits)})"
    node_types = args.get("node_types")
    if node_types:
        base += f" (types: {', '.join(node_types)})"
    return base


def _navigate_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    content = result.content or ""
    if "No children" in content or "no items" in content.lower():
        return "No children found"
    first_line = content.split("\n", 1)[0] if content else ""
    return first_line or "Navigation result"


def _lookup_args_summary(args: dict[str, Any]) -> str | None:
    identifiers = args.get("identifiers") or []
    if isinstance(identifiers, str):
        identifiers = [identifiers]
    if not identifiers:
        return "Lookup (no identifiers)"
    joined = ", ".join(str(i) for i in identifiers[:3])
    suffix = f" (+{len(identifiers) - 3} more)" if len(identifiers) > 3 else ""
    return f"Looked up: {joined}{suffix}"


# `Name :` is the fixed-width-padded label `Record.to_llm_context()` emits
# (see `entities.py`) inside the `context_block` `render_lookup_result`
# prints for each match — same convention as `_RECORD_NAME_RE` in
# `retrieval.py` and `tool_summarizer.py`. Matches are checked BEFORE the
# not-found check below: a batch of three hits plus one miss must summarise
# as found, not as "Not found or no access".
_LOOKUP_NAME_RE = re.compile(r"^Name\s*:\s*(.+)$", re.MULTILINE)
# Fallback count when a match has no name (e.g. a dict-shaped provider
# return that never populated `context_block`) — "Record ID" is present in
# both the `context_block` and structured-fallback renderings, so counting
# it can never join into a bare separator the way joining empty names would.
_LOOKUP_RECORD_ID_RE = re.compile(r"^Record ID\s*:", re.MULTILINE)


def _lookup_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    content = str(result.content) if result.content else ""
    if not content:
        return "No results"
    names = [n.strip() for n in _LOOKUP_NAME_RE.findall(content) if n.strip()]
    if names:
        shown = "; ".join(names[:2])
        suffix = f" (+{len(names) - 2} more)" if len(names) > 2 else ""
        return shown + suffix
    if "Not found" in content or "no access" in content.lower():
        return "Not found or no access"
    record_count = len(_LOOKUP_RECORD_ID_RE.findall(content))
    if record_count:
        return f"Found {record_count} record{'s' if record_count != 1 else ''}"
    if result.is_error:
        return "Lookup failed"
    return "Lookup result"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_identifiers(raw: Any) -> list[str]:
    """Accept a string or list of strings from the LLM, always return list[str]."""
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(i).strip() for i in raw if str(i).strip()]
    return []


def _flatten_row_ids(rows: Any) -> list[str]:
    """Every record/folder id in `rows`."""
    return [row.id for row in rows if row.is_record and row.id]


def _record_ids_in_view(view: NavigationView) -> list[str]:
    """Every fetchable Record ID this navigation showed the model.

    Only record/folder nodes — an app or recordGroup node id cannot be read
    by `knowledgegraph__fetch_record`. `view.rows` already includes every
    descendant a depth>=2 call surfaced (flat, not nested), so no separate
    recursion is needed here.
    """
    ids = _flatten_row_ids((*view.rows, *view.related))
    if view.current and view.current.is_record and view.current.id:
        ids.append(view.current.id)
    return ids


def _get_scoping(state: ChatState) -> tuple[list[str], list[str]]:
    """Return (connector_ids, kb_ids) — shim kept for the contract tests."""
    from .ops.scope import derive_scope
    scope = derive_scope(state)
    return list(scope.app_ids), list(scope.kb_ids)


def _time_range_error_message(error_json: str) -> str:
    """Extract the plain-text `message` field from a `parse_time_range()`
    error JSON string, matching navigate()'s other plain-text
    tuple[bool, str] error returns (search() returns the JSON as-is since
    its own contract is a raw string, but navigate()'s is bool + message)."""
    try:
        parsed = json.loads(error_json)
        return parsed.get("message", error_json) if isinstance(parsed, dict) else error_json
    except (json.JSONDecodeError, AttributeError):
        return error_json


def _copies_outside_selection(identifier: str, matches: list, *, connected_app: bool = False) -> str:
    """Where the readable copies of an issue key or URL live, when none of
    them is inside this conversation. Success, not a failed tool call.

    A connected app's limit is the most it may learn about, so a copy outside
    it is not named (``connected_app``)."""
    if connected_app:
        return f"{identifier} is outside what this conversation is limited to."
    places = []
    for match in matches:
        name = getattr(match, "name", None) or match.id
        where = getattr(match, "connector_name", None) or "another source"
        places.append(f"{name} ({where})")
    found = "; ".join(places)
    return (
        f"{identifier} is outside what this conversation is limited to. "
        f"A copy the user can open elsewhere: {found}."
    )


# ---------------------------------------------------------------------------
# Toolset
# ---------------------------------------------------------------------------


@ToolsetBuilder("KnowledgeGraph") \
    .in_group("Internal Tools") \
    .with_description("Navigate the knowledge graph — walk App → RecordGroup → Record → Child; resolve URLs and issue keys to record IDs") \
    .with_category(ToolsetCategory.UTILITY) \
    .with_auth([AuthBuilder.type("NONE").fields([])]) \
    .as_internal() \
    .as_essential() \
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/knowledge_hub.svg")) \
    .build_decorator()
class KnowledgeGraph:
    """Navigation and lookup tools for the agent knowledge graph."""

    def __init__(self, state: ChatState | None = None) -> None:
        self.state: ChatState | None = state

    @tool(
        path="/tools/knowledgegraph/navigate",
        short_description="Browse the knowledge graph by node (App → RecordGroup → Record → children)",
        description=(
            "Navigate the connected knowledge hierarchy and see breadcrumbs, related links, and Record IDs.\n\n"
            "Hierarchy levels (each node's children are the next level down):\n"
            "  Root → App (connector: Jira, Confluence, Drive, Slack, ...)\n"
            "  App → RecordGroup (project, space, drive, channel, repository, ...)\n"
            "  RecordGroup → Record/Folder (epic, page, file, ticket, message, ...)\n"
            "  Record/Folder → child Records (story, subtask, sub-page, attachment, ...)\n\n"
            "Use this when a question depends on structure rather than wording: what is under this epic, "
            "which pages sit in this space, what is linked to this ticket. Search finds records by content; "
            "only this shows how they relate.\n\n"
            "Call with no arguments to see all connected apps.\n"
            "Pass node_id to open any node.\n"
            "Pass node_types to filter children by type (recordGroup, record, folder).\n\n"
            "node_id is tolerant: passing a URL or issue key (PA-1787) resolves it automatically.\n\n"
            "Opening a record shows its own metadata — for a ticket that includes status, assignee, "
            "priority and dates — so a question about one record is often answered by this call alone. "
            "For its content, pass the Record ID to knowledgegraph__fetch_record.\n\n"
            "Pass depth=2 or depth=3 to see multiple levels in ONE call instead of navigating one level "
            "at a time — e.g. an app's spaces AND their pages, or an epic's stories AND their subtasks. "
            "Results are returned as a flat list sorted by creation date (latest first), with each row "
            "showing its level in the hierarchy. Use this whenever the question needs an overview of a "
            "hierarchy rather than a single node (status of a whole initiative, what a project contains "
            "end to end).\n\n"
            "Output always shows:\n"
            "  Path: breadcrumb trail with node IDs\n"
            "  Record ID / Node ID: stable id, pass back to navigate() or to fetch_record()\n"
            "  Children listing with record_id= or node_id= for each item, with a created: date "
            "when known (uploaded: for a knowledge-base file — an upload time, not a document date)\n"
            "  Related: cross-references (page 1 only, record nodes only)\n"
            "  Next: exactly what to call next\n\n"
            "Time-scoped browsing: pass created_after/created_before or modified_after/modified_before "
            "to filter children by source timestamp (see parameter descriptions for date format) — "
            "use this instead of fetching all children when the question specifies a date range. "
            "Inside a knowledge base these filter by when each file was uploaded. "
            "Use conservative ranges (pad by a day on each side) since source timestamps may differ "
            "from the user's timezone. If time-filtered results are empty or too few, widen the "
            "range or retry without time filters and filter manually from the listing."
        ),
        parameters=[
            ToolParameter(
                name="node_types",
                type=ParameterType.ARRAY,
                description=(
                    "Optional filter on which child node types to return. Values: "
                    "'recordGroup', 'record', 'folder'. Omit to return all children. "
                    "Use when you only need a specific type — e.g. "
                    "node_types=['recordGroup'] on an app to see only spaces/projects, "
                    "or node_types=['record'] on a recordGroup to skip sub-groups."
                ),
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="node_id",
                type=ParameterType.STRING,
                description=(
                    "ID of the node to open. Get from a previous navigate() output "
                    "(record_id= or node_id=), from capability_summary, or paste a URL/issue-key "
                    "directly — navigate() resolves it. Omit to see root apps."
                ),
                required=False,
            ),
            ToolParameter(
                name="page",
                type=ParameterType.INTEGER,
                description="Page number (1-based). Page >1 omits header/breadcrumbs/related.",
                required=False,
                default=1,
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Children per page (50–200, default 50).",
                required=False,
                default=50,
            ),
            ToolParameter(
                name="depth",
                type=ParameterType.INTEGER,
                description=(
                    "How many levels of children to return in this one call (1-3, default 1). "
                    "depth=1 returns direct children only. depth=2 also fetches each child's "
                    "own children. depth=3 goes one level deeper. Results are returned as a "
                    "flat list sorted by creation date (latest first), with each row showing "
                    "its level in the hierarchy. Works for any parent type (app, recordGroup, "
                    "record, folder). Use depth=2/3 for hierarchy-overview questions instead "
                    "of calling navigate() once per level."
                ),
                required=False,
                default=1,
            ),
            ToolParameter(
                name="created_after",
                type=ParameterType.STRING,
                description=(
                    "Only show children whose source creation date is on or after this. "
                    "ISO 8601 format: 'YYYY-MM-DD' (e.g. '2026-01-15') or full datetime with "
                    "timezone (e.g. '2026-01-15T00:00:00Z'). YYYY-MM-DD is interpreted as the "
                    "start of that day in UTC. Use for questions like 'pages created this month' "
                    "or 'tickets filed after January'. Prefer conservative (wider) ranges — pad "
                    "by a day when the user's timezone is unknown. If results are sparse, retry "
                    "with a wider range or without time filters before concluding nothing exists."
                ),
                required=False,
            ),
            ToolParameter(
                name="created_before",
                type=ParameterType.STRING,
                description=(
                    "Only show children whose source creation date is on or before this "
                    "(inclusive of the whole day for a date-only value). ISO 8601 format: "
                    "'YYYY-MM-DD' or full datetime with timezone. Pair with created_after for "
                    "a date window, e.g. created_after='2026-01-01', created_before='2026-03-31' "
                    "for Q1 2026."
                ),
                required=False,
            ),
            ToolParameter(
                name="modified_after",
                type=ParameterType.STRING,
                description=(
                    "Only show children last modified at the source on or after this date. "
                    "ISO 8601 format: 'YYYY-MM-DD' or full datetime with timezone. Use for "
                    "questions about recently changed content ('pages updated this week')."
                ),
                required=False,
            ),
            ToolParameter(
                name="modified_before",
                type=ParameterType.STRING,
                description=(
                    "Only show children last modified at the source on or before this date "
                    "(inclusive). ISO 8601 format: 'YYYY-MM-DD' or full datetime with timezone. "
                    "Pair with modified_after for a modification date window."
                ),
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=_navigate_args_summary,
        result_summary=_navigate_result_summary,
    )
    async def navigate(
        self,
        node_id: str | None = None,
        page: int = 1,
        limit: int = 50,
        depth: int = 1,
        created_after: str | None = None,
        created_before: str | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        node_types: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Walk the knowledge graph hierarchy."""
        if not self.state:
            return False, "Knowledge graph tool state not initialized."

        state = self.state
        graph_provider = state.get("graph_provider")
        org_id = state.get("org_id", "")
        user_id = state.get("user_id", "")

        if not graph_provider:
            return False, "Graph provider not available."

        frontend_url = await resolve_frontend_url(state.get("config_service"))

        # Normalize
        node_id = node_id.strip() if node_id else None
        node_types = node_types if node_types else None

        page = max(1, page)
        limit = min(max(50, limit), 200)
        depth = min(max(1, depth), 3)

        # Same parser knowledgegraph__search uses for its own
        # created_after/created_before/modified_after/modified_before —
        # rejects timezone-naive datetimes, validates after <= before, and
        # rejects a future created_after, so navigate() gets identical
        # (and identically-worded) validation instead of a second,
        # looser ISO-parsing implementation.
        from app.agents.actions.knowledge_graph.ops.time_range import parse_time_range
        time_range, time_error = parse_time_range(
            created_after=created_after,
            created_before=created_before,
            modified_after=modified_after,
            modified_before=modified_before,
        )
        if time_error is not None:
            return False, _time_range_error_message(time_error)
        created_at, updated_at = time_range_to_kh_filters(time_range)

        # TEMPORARY token-savings experiment (opt-in, disabled by default —
        # see `ChatQuery.enableRecordIdShortening`): node_id may be a short
        # `R<n>` label this same shortener minted in an earlier tool call
        # (search, a previous navigate, lookup_record, list_files) —
        # resolve it back to the full id before it reaches the graph
        # provider. IDs that were never shortened pass through `.resolve()`
        # unchanged. Skipped entirely when the flag is off.
        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        record_id_shortener = get_record_id_shortener_if_enabled(state)
        if node_id and record_id_shortener is not None:
            node_id = record_id_shortener.resolve(node_id)

        # Get user key
        user_key = await _get_user_key(graph_provider, user_id)
        if not user_key:
            return False, "User not found."

        # Build catalog (cached in state)
        catalog = await ConnectorCatalog.build(
            state,
            graph_provider=graph_provider,
            user_key=user_key,
            org_id=org_id,
        )

        # Resolve connector/kb scope from agent config / filters
        scope = await resolve_scope(state)
        connector_ids = browsable_app_ids(state, scope)

        # A selection below app level is already the scope; a connector
        # filter would hide a collection folder's children.
        if not connector_ids and not _has_selection(state.get("filters") or {}):
            if not state.get("has_knowledge"):
                return False, "No knowledge sources configured for this agent."
            # Nothing picked: no connector filter, so the listing gates by the
            # user's own access. The catalog would leave out knowledge bases.
            # A picked turn with nothing browsable (only attachments) keeps the
            # catalog; its rows are re-checked against the turn's limit below.
            if turn_picked_sources(state):
                connector_ids = catalog.connector_ids()

        # If node_id looks like a URL or issue key, try to resolve it first
        if node_id:
            from .identifiers import _BARE_ISSUE_KEY, _is_url
            if _is_url(node_id) or _BARE_ISSUE_KEY.match(node_id):
                resolver = RecordResolver(
                    graph_provider=graph_provider,
                    catalog=catalog,
                    org_id=org_id,
                    user_id=user_id,
                    user_key=user_key,
                    folder_mime_types=FOLDER_MIME_TYPES,
                    agent_connector_ids=connector_ids,
                    frontend_url=frontend_url,
                    excluded_app_ids=excluded_app_ids(state),
                )
                result = await resolver.resolve_many([node_id])
                if result.matches:
                    # The same issue key can exist on more than one connector.
                    # The copy inside this conversation wins; another connector's
                    # copy is named so the miss is not a broken tool.
                    inside = await ids_within_scope(
                        state, graph_provider, user_key, org_id,
                        [match.id for match in result.matches],
                    )
                    chosen = next((match for match in result.matches if match.id in inside), None)
                    if chosen is None:
                        return True, _copies_outside_selection(
                            node_id, result.matches,
                            connected_app=connection_sources_of(state.get("filters")) is not None,
                        )
                    node_id = chosen.id

        refusal = await selection_browse_refusal(state, graph_provider, user_key, org_id, node_id)
        if refusal:
            return False, refusal

        navigator = GraphNavigator(
            graph_provider=graph_provider,
            user_id=user_id,
            user_key=user_key,
            org_id=org_id,
            frontend_url=frontend_url,
            excluded_app_ids=excluded_app_ids(state),
        )

        app_names = {c.id: c.name for c in catalog.connectors}

        try:
            view = await navigator.navigate(
                node_id=node_id,
                name_filter=None,
                page=page,
                limit=limit,
                connector_ids=connector_ids or None,
                depth=depth,
                created_at=created_at,
                updated_at=updated_at,
                node_types=node_types,
                app_names=app_names,
            )
            # The listing names every ancestor the user can open, and every
            # child under the opened node. Neither is the conversation's
            # limit: a parent above a selected group would be named and
            # offered as the place to see siblings, and a child the user
            # cannot read would be offered to fetch.
            ancestors, rows, related = await navigation_nameable_ids(
                state, graph_provider, user_key, org_id,
                ancestor_ids=[crumb.id for crumb in view.breadcrumbs],
                row_ids=[row.id for row in view.rows],
                related_ids=[row.id for row in view.related],
            )
            if ancestors is not None:
                view.breadcrumbs = [crumb for crumb in view.breadcrumbs if crumb.id in ancestors]
            view.rows = [row for row in view.rows if row.id in rows]
            view.related = [row for row in view.related if row.id in related]
            limit_sources = connection_sources_of(state.get("filters"))
            await keep_context_blocks_inside_connection(
                [(view.current.id if view.current else None, view)], graph_provider,
                state.get("user_id", ""), org_id, limit_sources,
            )
            await keep_row_summaries_inside_connection(
                [*view.rows, *view.related], graph_provider, state.get("user_id", ""), org_id, limit_sources,
            )
        except Exception:
            logger.exception("navigate failed for node_id=%s", node_id)
            return False, "Navigation failed — try again or use a different node_id."

        text, emitted = render_navigation_view_with_ids(view, page, record_id_shortener)
        fetchable = set(_record_ids_in_view(view))
        remember_record_ids(
            state,
            [rid for rid in emitted if rid in fetchable],
            names={n.id: n.name for n in (*view.rows, *view.related, *([view.current] if view.current else []))},
        )

        # Sparse-result retry nudge: only fires when time filters were
        # actually applied, so a plain (unfiltered) empty/near-empty listing
        # isn't told to "retry without time filters" for no reason.
        had_time_filters = bool(created_at or updated_at)
        if had_time_filters and len(view.rows) < 3:
            text += (
                "\n\nHint: few/no results with this time range. Try widening the "
                "range (pad +/- 1 day) or retry without time filters to see all "
                "children, then filter manually."
            )
        return True, text

    @tool(
        path="/tools/knowledgegraph/lookup_record",
        short_description="Resolve a URL, issue key, or external ID to a Record ID",
        description=(
            "Find a specific record by URL, Jira/Linear issue key (PA-1787), or external ID — the "
            "first step whenever the question names one. Returns that record's metadata (for a "
            "ticket: status, assignee, priority, dates) plus its Record ID, which navigate() takes "
            "to list what is under it and fetch_record() takes to read its content.\n\n"
            "Supported: Jira URLs and keys, Confluence page URLs and IDs, Google Drive links "
            "and file IDs, Slack permalinks, Linear issues, Notion pages, ServiceNow sys_id, "
            "SharePoint URLs, Gmail/Outlook URLs, and any connector whose records index webUrl.\n\n"
            "Pass multiple identifiers to batch-resolve them in one call.\n"
            "identifiers may be a list or a single string.\n\n"
            "Resolution searches across ALL connectors you have access to — a Jira key found "
            "inside a Confluence page resolves even when the session filter is Confluence-only.\n\n"
            "Zero accessible results and not-found are the same response — use navigate() to "
            "confirm the record exists before assuming it is missing."
        ),
        parameters=[
            ToolParameter(
                name="identifiers",
                type=ParameterType.ARRAY,
                description=(
                    "List of URLs, issue keys (e.g. PA-1787), or external IDs to resolve. "
                    "May also be a single string. Maximum 10."
                ),
                required=True,
                items={"type": "string"},
            ),
            ToolParameter(
                name="connector_name",
                type=ParameterType.STRING,
                description=(
                    "Optional connector hint (e.g. 'JIRA', 'CONFLUENCE', 'GOOGLE_DRIVE') to "
                    "prioritise resolution order. Cannot widen beyond accessible connectors."
                ),
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=_lookup_args_summary,
        result_summary=_lookup_result_summary,
    )
    async def lookup_record(
        self,
        identifiers: list[str] | str,
        connector_name: str | None = None,
    ) -> tuple[bool, str]:
        """Resolve identifiers to Record IDs."""
        if not self.state:
            return False, "Knowledge graph tool state not initialized."

        state = self.state
        graph_provider = state.get("graph_provider")
        org_id = state.get("org_id", "")
        user_id = state.get("user_id", "")

        if not graph_provider:
            return False, "Graph provider not available."

        idents = _normalize_identifiers(identifiers)
        if not idents:
            return False, "No identifiers provided."
        idents = idents[:10]

        user_key = await _get_user_key(graph_provider, user_id)
        if not user_key:
            return False, "User not found."

        # Build catalog (cached in state)
        catalog = await ConnectorCatalog.build(
            state,
            graph_provider=graph_provider,
            user_key=user_key,
            org_id=org_id,
        )

        if catalog.is_empty():
            if not state.get("has_knowledge"):
                return False, "No knowledge sources configured for this agent."
            logger.warning(
                "lookup_record: catalog is empty — apps=%r, kb=%r, agent_knowledge=%d entries",
                state.get("apps"), state.get("kb"),
                len(state.get("agent_knowledge") or []),
            )
            return False, _NOT_FOUND_MSG

        # Agent-scoped connector ids (for ordering priority in resolver)
        scope = await resolve_scope(state)
        connector_ids = browsable_app_ids(state, scope)

        resolver = RecordResolver(
            graph_provider=graph_provider,
            catalog=catalog,
            org_id=org_id,
            user_id=user_id,
            user_key=user_key,
            folder_mime_types=FOLDER_MIME_TYPES,
            agent_connector_ids=connector_ids,
            connector_name_hint=connector_name,
            frontend_url=await resolve_frontend_url(state.get("config_service")),
            excluded_app_ids=excluded_app_ids(state),
        )

        try:
            result = await resolver.resolve_many(idents)
            # A match outside what the turn is limited to is not a match.
            inside = await ids_within_scope(
                state, graph_provider, user_key, org_id, [m.id for m in result.matches],
            )
        except Exception:
            logger.exception("lookup_record resolve_many failed for %s", idents)
            return False, _NOT_FOUND_MSG
        if len(inside) < len(result.matches):
            kept = [m for m in result.matches if m.id in inside]
            result = result.model_copy(update={"matches": kept, "ambiguous": result.ambiguous and len(kept) > 1})
        await keep_context_blocks_inside_connection(
            [(m.id, m) for m in result.matches], graph_provider, state.get("user_id", ""), org_id,
            connection_sources_of(state.get("filters")),
        )

        # TEMPORARY token-savings experiment (opt-in, disabled by default —
        # see `ChatQuery.enableRecordIdShortening`) — see `RecordIdShortener`
        # in `utils/chat_helpers.py`. Same shared shortener as navigate()/
        # search()/list_files(), keyed off tool_state so any tool the model
        # calls first mints the shared mapping.
        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        record_id_shortener = get_record_id_shortener_if_enabled(state)

        text, emitted = render_lookup_result_with_ids(result, record_id_shortener)
        remember_record_ids(
            state,
            emitted,
            names={m.id: m.name for m in result.matches},
        )
        # "Zero accessible results" is a legitimate lookup outcome, not a
        # tool failure — `success=False` renders as a red failed-tool-call
        # in the frontend and tells the model "the tool broke" rather than
        # "try navigate() instead". Reserve False for the exception path
        # above and the pre-flight guards further up.
        return True, text or _NOT_FOUND_MSG


    # -----------------------------------------------------------------------
    # search — semantic full-text search over indexed knowledge
    # -----------------------------------------------------------------------

    @tool(
        path="/tools/knowledgegraph/search",
        short_description="Semantic search over indexed knowledge (returns ranked content blocks)",
        description=(
            "Search indexed knowledge by meaning — returns the top-ranked content blocks from "
            "the best-matching records across connected apps and knowledge bases.\n\n"
            "Important constraints — read before deciding to call:\n"
            "  • Returns a RANKED SAMPLE, not a complete list. Other records may match.\n"
            "  • Returns a few blocks per record, not all blocks. Full content requires "
            "fetch_record.\n"
            "  • Does not return every record that has the words — it is top-k by relevance.\n\n"
            "When to combine with other tools:\n"
            "  - Call navigate() on any returned Record ID to see its children, siblings, "
            "or linked records that search structurally cannot surface.\n"
            "  - Search results may include a Location breadcrumb showing the hierarchy "
            "(e.g. 'Confluence > Space > Parent Page (Record ID: ...)'). If a parent "
            "container looks relevant (e.g. a 'Daily Bug Bash' parent page), pass its "
            "Record ID to navigate(node_id=...) to browse siblings and children the "
            "search could not surface.\n"
            "  - For questions that must be exhaustive ('all', 'how many', 'every'), do NOT "
            "count search results — navigate the record group or scope list_files to one "
            "source instead.\n\n"
            "Parallel searches: pass one source_id per call and run calls in parallel for "
            "per-source recall. Omit source_ids to search all accessible sources in one call. "
            "If searches limited with source_ids find nothing, search once more with "
            "source_ids omitted before concluding the information does not exist: a "
            "source's name rarely says everything it holds. Skip that only when the user "
            "asked to search just those sources.\n\n"
            "Time filtering: use created_after/created_before to scope by source creation date, "
            "and modified_after/modified_before to scope by source last-modified date. "
            "For connected apps these are the source system's timestamps (when the file was "
            "created in Drive, when the Jira ticket was filed), not when PipesHub indexed it. "
            "A knowledge-base upload is dated as its record header shows: by a date the "
            "document states when one was found in it, otherwise only by the upload (the "
            "file's last-modified time or the upload time), which is not when it was written. "
            "A date filter can keep an upload dated only by its upload although it was written "
            "outside the range, so check its header before treating it as in range.\n\n"
            "Time-filter guidance:\n"
            "  - Prefer conservative (wider) date ranges. 'This quarter' means the full "
            "quarter boundaries, not today's date as the end.\n"
            "  - When the user says 'recent' or 'lately' without a specific date, use "
            "modified_after with a generous window (e.g. 30-90 days back) rather than a "
            "narrow one.\n"
            "  - If a time-scoped search returns no results or too few results, retry WITHOUT "
            "the date filters to check whether relevant records exist outside the time window "
            "before concluding nothing exists. The user's time reference may not match the "
            "source timestamp exactly (e.g. a document discussed in Q1 may have been created "
            "in the prior quarter).\n"
            "  - Omit date filters entirely when the question has no temporal signal — most "
            "queries do not need them."
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query — what you are looking for.",
                required=True,
            ),
            ToolParameter(
                name="source_ids",
                type=ParameterType.ARRAY,
                description=(
                    "Filter to specific sources by ID. Accepts both app-connector IDs and "
                    "KB-collection IDs — pass whichever you got from the Knowledge Sources "
                    "section. Pass a single ID per call and run calls in parallel for "
                    "per-source searches. Omit to search all accessible sources."
                ),
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="created_after",
                type=ParameterType.STRING,
                description=(
                    "Only include records created at the source on or after this date. "
                    "ISO 8601 format: 'YYYY-MM-DD' (e.g. '2026-01-15') or full datetime "
                    "with timezone (e.g. '2026-01-15T00:00:00Z'). "
                    "For connected apps this is the creation date at the source "
                    "(Google Drive, Jira, Confluence, etc.), not when it was indexed; for a "
                    "knowledge-base upload, the date its record header shows. "
                    "Use for questions like 'files created this quarter' or 'tickets opened "
                    "after January'. YYYY-MM-DD is interpreted as the start of that day in UTC."
                ),
                required=False,
            ),
            ToolParameter(
                name="created_before",
                type=ParameterType.STRING,
                description=(
                    "Only include records created at the source on or before this date "
                    "(inclusive). ISO 8601 format: 'YYYY-MM-DD' or full datetime with "
                    "timezone. YYYY-MM-DD includes the entire day. Pair with created_after "
                    "for a date window. Example: created_after='2026-01-01', "
                    "created_before='2026-03-31' restricts to Q1 2026."
                ),
                required=False,
            ),
            ToolParameter(
                name="modified_after",
                type=ParameterType.STRING,
                description=(
                    "Only include records last modified at the source on or after this date. "
                    "ISO 8601 format: 'YYYY-MM-DD' or full datetime with timezone. "
                    "Use for questions about recently changed content: 'documents updated "
                    "this week', 'pages modified since last Monday'. YYYY-MM-DD is "
                    "interpreted as the start of that day in UTC."
                ),
                required=False,
            ),
            ToolParameter(
                name="modified_before",
                type=ParameterType.STRING,
                description=(
                    "Only include records last modified at the source on or before this date "
                    "(inclusive). ISO 8601 format: 'YYYY-MM-DD' or full datetime with "
                    "timezone. YYYY-MM-DD includes the entire day. Pair with modified_after "
                    "for a modification date window."
                ),
                required=False,
            ),
            ToolParameter(
                name="entity_ids",
                type=ParameterType.ARRAY,
                description=(
                    "Optional entityIds returned by search_entities in this turn — "
                    "restricts the search to content connected to those entities. Accepts "
                    "department/category/subcategory/topic/language entities and record_group "
                    "entities (a Drive folder, Jira project, Slack channel, ...; restricts to "
                    "that group's records). Nothing is detected from the query text "
                    "automatically. If nothing matches inside the entity, the result says the "
                    "entity filter was dropped."
                ),
                required=False,
                items={"type": "string"},
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searched for "{args.get("query", "")}"'
            + (f' in {len(args.get("source_ids", []))} source(s)'
               if args.get("source_ids") else "")
            if args.get("query") else None
        ),
        result_summary=lambda args, result: _timeline_result_summary(args, result),
        display_name="Searched the knowledge base",
    )
    async def search(
        self,
        query: str | None = None,
        source_ids: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        entity_ids: list[str] | None = None,
    ) -> str | list[Part]:
        """Semantic search — calls ops/search.py execute_search."""
        from .ops.search import execute_search
        return await execute_search(
            self.state,
            query=query,
            source_ids=source_ids,
            created_after=created_after,
            created_before=created_before,
            modified_after=modified_after,
            modified_before=modified_before,
            entity_ids=entity_ids,
        )

    # -----------------------------------------------------------------------
    # search_entities — essential (turn-0) entity discovery
    # -----------------------------------------------------------------------

    @tool(
        path="/tools/knowledgegraph/search_entities",
        short_description="Find departments, categories, topics, languages, record groups and record titles the user can access",
        description=(
            "Semantic lookup of knowledge-graph entities by name or description: departments, "
            "categories, subcategories, topics, languages, record groups (a project, space, "
            "folder, channel, ...) and record titles. Only entities backed by records you can "
            "access are returned.\n\n"
            "Call it when the question names one of these — a team, a topic, a category, a "
            "project or space, a document title — to find the matching entity, alongside or "
            "before a content search.\n\n"
            "Results are best match first. Each has entityId, entityType, name and the apps it "
            "appears in. The "
            "top few department/category/topic/language/record_group results also include a "
            "short preview of records (newest first) with moreRecords when there are more.\n\n"
            "Next steps:\n"
            "  - knowledgegraph__find_records_by_entity(entity_id=...) lists the records "
            "connected to one entity, newest first, with paging.\n"
            "  - search(query=..., entity_ids=[...]) searches content inside a "
            "department/category/subcategory/topic/language or record_group entity.\n"
            "  - A 'record' result's entityId is a Record ID: pass it straight to "
            "fetch_record or navigate."
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The entity name or description to look for — e.g. 'legal', 'Q3 roadmap', 'the design team', 'onboarding checklist'.",
                required=True,
            ),
            ToolParameter(
                name="entity_types",
                type=ParameterType.ARRAY,
                description=(
                    "Optional filter on entity type: 'department', 'category', 'subcategory', "
                    "'topic', 'language', 'record_group', 'record'. Omit to search all types."
                ),
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="top_k",
                type=ParameterType.INTEGER,
                description="Maximum entities to return (1-25, default 10).",
                required=False,
                default=10,
            ),
            ToolParameter(
                name="named_entity_kinds",
                type=ParameterType.ARRAY,
                description=(
                    "Optional kinds when searching extracted named entities "
                    "(person, organization, product, date, currency, ...). "
                    "Ignored unless named-entity extraction is enabled."
                ),
                required=False,
                items={"type": "string"},
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searched entities for "{args.get("query", "")}"' if args.get("query") else None
        ),
        result_summary=lambda args, result: (
            _search_entities_result_summary(args, result)
        ),
        display_name="Searched knowledge graph entities",
    )
    async def search_entities(
        self,
        query: str | None = None,
        entity_types: list[str] | None = None,
        top_k: int = 10,
        named_entity_kinds: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Entity discovery — calls ops/entity_discovery.py."""
        from .ops.entity_discovery import execute_search_entities
        return await execute_search_entities(
            self.state,
            query=query,
            entity_types=entity_types,
            top_k=top_k,
            named_entity_kinds=named_entity_kinds,
        )

    async def _search_entities_with_queries(
        self,
        query: str | None = None,
        entity_types: list[str] | None = None,
        top_k: int = 10,
        named_entity_kinds: list[str] | None = None,
        queries: list[str] | None = None,
    ) -> tuple[bool, str]:
        """``search_entities`` where ``search_by_entities`` is offered
        (``entity_search_surface``): several queries per call, and kinds it
        cannot serve explained."""
        from .ops.entity_discovery import execute_search_entities
        return await execute_search_entities(
            self.state,
            query=query,
            entity_types=entity_types,
            top_k=top_k,
            named_entity_kinds=named_entity_kinds,
            queries=queries,
            entity_search_offered=True,
        )

    # -----------------------------------------------------------------------
    # find_records_by_entity — granted once search_entities has been called
    # -----------------------------------------------------------------------

    @tool(
        path="/tools/knowledgegraph/find_records_by_entity",
        short_description="List the records connected to one entity that the user can access",
        description=(
            "Given an entityId from search_entities, list the records connected to it that you "
            "can access, newest first: every record tagged with a department, category, "
            "subcategory, topic or language, every record directly inside a record_group, or "
            "the record itself for a 'record' entity.\n\n"
            "Use it when the question is about an entity's records as a whole ('what's tagged "
            "Q3 Roadmap', 'what's in this Drive folder', 'Legal's documents'). For content "
            "inside an entity, use search(entity_ids=[...]) instead.\n\n"
            "Pages with cursor: when more records may exist the output ends with the exact "
            "call for the next page. There are no totals. 'No accessible records found' is the "
            "same response whether the entity has no records or none you can access; paging "
            "past the last page says so separately."
        ),
        parameters=[
            ToolParameter(
                name="entity_id",
                type=ParameterType.STRING,
                description="The entityId from search_entities.",
                required=True,
            ),
            ToolParameter(
                name="entity_type",
                type=ParameterType.STRING,
                description=(
                    "The entity's type: 'department', 'category', 'subcategory', 'topic', "
                    "'language', 'record_group' or 'record'. Optional when the entityId came "
                    "from search_entities in this turn."
                ),
                required=False,
            ),
            ToolParameter(
                name="record_types",
                type=ParameterType.ARRAY,
                description="Optional filter by record type (e.g. 'FILE', 'MAIL', 'TICKET', 'CONFLUENCE_PAGE').",
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Records per page (1-50, default 20).",
                required=False,
                default=20,
            ),
            ToolParameter(
                name="cursor",
                type=ParameterType.STRING,
                description=(
                    "Cursor from the previous page's output, copied exactly as printed "
                    "(a plain integer offset). Omit for the first page."
                ),
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Found records for entity {args.get("entity_id", "")}' if args.get("entity_id") else None
        ),
        result_summary=lambda args, result: (
            _find_records_by_entity_result_summary(args, result)
        ),
        display_name="Found records connected to entity",
    )
    async def find_records_by_entity(
        self,
        entity_id: str | None = None,
        entity_type: str | None = None,
        record_types: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[bool, str]:
        """Entity-to-record listing — calls ops/entity_records.py."""
        from .ops.entity_records import execute_find_records_by_entity
        return await execute_find_records_by_entity(
            self.state,
            entity_id=entity_id,
            entity_type=entity_type,
            record_types=record_types,
            limit=limit,
            cursor=cursor,
        )

    @tool(
        path="/tools/knowledgegraph/find_records_by_value",
        short_description="Find accessible records that mention a typed value",
        description=(
            "Find records the user can access that mention a typed value: a date range, "
            "an amount in a currency, a quantity, or a percentage. Requires named-entity "
            "extraction to be enabled. A match set that is too large is rejected rather "
            "than truncated."
        ),
        parameters=[
            ToolParameter(name="kinds", type=ParameterType.ARRAY, description="Optional entity kinds.", required=False, items={"type": "string"}),
            ToolParameter(name="name", type=ParameterType.STRING, description="Optional name prefix.", required=False),
            ToolParameter(name="date_from", type=ParameterType.INTEGER, description="Range start, epoch milliseconds.", required=False),
            ToolParameter(name="date_to", type=ParameterType.INTEGER, description="Range end, epoch milliseconds.", required=False),
            ToolParameter(name="amount_min", type=ParameterType.FLOAT, description="Minimum amount.", required=False),
            ToolParameter(name="amount_max", type=ParameterType.FLOAT, description="Maximum amount.", required=False),
            ToolParameter(name="currency", type=ParameterType.STRING, description="ISO-4217 currency code.", required=False),
            ToolParameter(name="quantity_min", type=ParameterType.FLOAT, description="Minimum quantity in SI units.", required=False),
            ToolParameter(name="quantity_max", type=ParameterType.FLOAT, description="Maximum quantity in SI units.", required=False),
            ToolParameter(name="dimension", type=ParameterType.STRING, description="Quantity dimension.", required=False),
            ToolParameter(name="percent_min", type=ParameterType.FLOAT, description="Minimum percentage as a fraction: 0.2 for 20%.", required=False),
            ToolParameter(name="percent_max", type=ParameterType.FLOAT, description="Maximum percentage as a fraction: 0.2 for 20%.", required=False),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        display_name="Found records by typed value",
    )
    async def find_records_by_value(
        self,
        kinds: list[str] | None = None,
        name: str | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        currency: str | None = None,
        quantity_min: float | None = None,
        quantity_max: float | None = None,
        dimension: str | None = None,
        percent_min: float | None = None,
        percent_max: float | None = None,
    ) -> tuple[bool, str]:
        from .ops.values import execute_find_records_by_value
        return await execute_find_records_by_value(
            self.state,
            kinds=kinds,
            name=name,
            date_from=date_from,
            date_to=date_to,
            amount_min=amount_min,
            amount_max=amount_max,
            currency=currency,
            quantity_min=quantity_min,
            quantity_max=quantity_max,
            dimension=dimension,
            percent_min=percent_min,
            percent_max=percent_max,
        )

    @tool(
        path="/tools/knowledgegraph/find_records_by_person",
        short_description="List the records a person is assigned, reported, created, leads or is mentioned in",
        description=(
            "List the records the connectors link to a person by role (assignee, reporter, "
            "creator, lead, mentioned, involved) that you can read, newest first. Use it for "
            "\"tickets assigned to Priya\", \"what did Darshan report\", \"issues created by "
            "dev@acme.com\", \"projects Sam leads\", \"my tickets\" (person=\"me\"). It uses "
            "the links the connectors write (mentioned/involved come from indexing): for any "
            "other record whose text mentions a name, search instead.\n\n"
            "Pass a full name or an email; several people with that name are listed for you to "
            "pick by email. Pages with cursor, the next call printed in the output; \"That is "
            "all N\" means the list is complete."
        ),
        parameters=[
            ToolParameter(
                name="person",
                type=ParameterType.STRING,
                description="The person's full name or email, or \"me\" for the user asking.",
                required=True,
            ),
            ToolParameter(
                name="roles",
                type=ParameterType.ARRAY,
                description="Optional roles: assignee, reporter, creator, lead, mentioned, involved. Omit for all.",
                required=False,
                items={
                    "type": "string",
                    "enum": ["assignee", "reporter", "creator", "lead", "mentioned", "involved"],
                },
            ),
            ToolParameter(
                name="record_types",
                type=ParameterType.ARRAY,
                description="Optional filter by record type (e.g. 'TICKET', 'PROJECT').",
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Records per page (1-50, default 20).",
                required=False,
                default=20,
            ),
            ToolParameter(
                name="cursor",
                type=ParameterType.STRING,
                description="Cursor from the previous page's output, copied exactly. Omit for the first page.",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Found records linked to {args.get("person", "")}' if args.get("person") else None
        ),
        result_summary=lambda args, result: (
            _find_records_by_entity_result_summary(args, result)
        ),
        display_name="Found records linked to a person",
    )
    async def find_records_by_person(
        self,
        person: str | None = None,
        roles: list[str] | None = None,
        record_types: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[bool, str]:
        from .ops.people import execute_find_records_by_person
        return await execute_find_records_by_person(
            self.state, person=person, roles=roles, record_types=record_types, limit=limit, cursor=cursor,
        )

    # -----------------------------------------------------------------------
    # search_by_entities — offered only where the deployment turns it on
    # -----------------------------------------------------------------------

    @tool(
        path="/tools/knowledgegraph/search_by_entities",
        short_description="Search inside the records that mention named people, organizations, products or places",
        description=(
            "Search inside the records that mention specific named things: people, customers, "
            "organizations, products, projects, events or places. Pass their names; each is linked to "
            "the entities extracted from the documents: an exact name settles it, and otherwise the "
            "look-alikes found are checked, so 'Acme Robotics' never returns 'Acme Financial'. "
            "The result opens with how each name was resolved (and which look-alikes are not the "
            "same), then the best passages for query inside the readable records that mention them, "
            "cited like search results.\n\n"
            "Use it when the question names one or more specific entities, especially to find where "
            "two or more appear together: match=\"all\" keeps records that mention every name, most "
            "relevant where they appear within a few blocks of each other. Pass several names in one "
            "call rather than one call each. Example: mentions=[{\"name\": \"Contoso\", \"kind\": "
            "\"organization\"}, {\"name\": \"Northwind CRM\", \"kind\": \"product\"}], "
            "match=\"all\", query=\"migration cutover\".\n\n"
            "Do not use it for topics, ideas or document titles (use search or search_entities), or "
            "for dates and amounts (use find_records_by_value). A name on most records (the company's "
            "own name, a first name) narrows little on its own; pair it with a rarer name or a query. "
            "People are found by their name as written, or by a first name that starts a full one; a "
            "surname alone is not looked up. For what a person is assigned, reported, created or "
            "leads, use knowledgegraph__find_records_by_person. Only records indexed while named-entity extraction was on "
            "are linked to names, so confirm a name that is not found with search before saying it "
            "is absent."
        ),
        parameters=[
            ToolParameter(
                name="mentions",
                type=ParameterType.ARRAY,
                description=(
                    "1-5 names to look up, each {\"name\": ..., \"kind\": ...}; kind is optional "
                    "and is what you believe the name is."
                ),
                required=True,
                items={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The name as a document would write it."},
                        "kind": {"type": "string", "enum": list(MENTION_KIND_HINTS)},
                    },
                    "required": ["name"],
                },
            ),
            ToolParameter(
                name="match",
                type=ParameterType.STRING,
                description="'any' (default): records mentioning any name; 'all': records mentioning every name.",
                required=False,
                enum=["any", "all"],
            ),
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description=(
                    "What to look for inside those records, in a document's words. Omit to rank the "
                    "passages that mention the names."
                ),
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: _search_by_entities_args_summary(args),
        result_summary=lambda args, result: _search_by_entities_result_summary(args, result),
        display_name="Searched records mentioning named entities",
    )
    async def search_by_entities(
        self,
        mentions: list[dict[str, Any]] | None = None,
        match: str | None = None,
        query: str | None = None,
    ) -> tuple[bool, str] | str | list[Part]:
        """Entity-linked search — calls ops/entity_search.py."""
        from .ops.entity_search import execute_search_by_entities
        return await execute_search_by_entities(self.state, mentions=mentions, match=match, query=query)

    def tools_for_request(self, tools: list[Tool]) -> list[Tool]:
        """This request's tools: ``search_by_entities`` and the descriptions
        that go with it only where the deployment offers it, and the
        named-entity tools only where extraction is on
        (``entity_search_surface``); ``search_timeline`` unless
        ``TIMELINE_SUMMARIES`` is false."""
        from .entity_search_surface import tools_for_request
        from .ops.timeline import without_timeline_unless_enabled
        tools = without_timeline_unless_enabled(tools)
        return tools_for_request(
            self, tools,
            offered=entity_search_offered_in(self.state),
            named_entities=named_entities_enabled_in(self.state),
        )

    # -----------------------------------------------------------------------
    # list_files — name-based search / browse (metadata only)
    # -----------------------------------------------------------------------

    @tool(
        path="/tools/knowledgegraph/list_files",
        short_description="Search or browse indexed items by name (returns metadata, not content)",
        description=(
            "Find records by name across all connected sources, or browse to see what exists "
            "under a specific source.\n\n"
            "Returns record metadata only (Record IDs, types, names). No content — pass a "
            "Record ID to fetch_record to read it, or to navigate() to see its children.\n\n"
            "When to use versus other tools:\n"
            "  - You know part of a name → use this.\n"
            "  - You want to explore structure (what is under an epic, a space) → use navigate().\n"
            "  - You want content → use search() first, then fetch_record() if needed.\n\n"
            "Scope with source_ids to restrict to one KB or connector."
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Name search query (2–500 chars). Required — to browse, use navigate().",
                required=True,
            ),
            ToolParameter(
                name="source_ids",
                type=ParameterType.ARRAY,
                description=(
                    "Filter to specific sources by ID. Accepts both app-connector IDs and "
                    "KB-collection IDs. Get IDs from the Knowledge Sources section. "
                    "Omit to search across all accessible sources."
                ),
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="node_types",
                type=ParameterType.ARRAY,
                description="Filter by node type: 'record', 'folder', 'recordGroup', 'app'.",
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="record_types",
                type=ParameterType.ARRAY,
                description="Filter by record type (e.g. 'CONFLUENCE_PAGE', 'FILE', 'TICKET').",
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="page",
                type=ParameterType.INTEGER,
                description="Page number (1-based).",
                required=False,
                default=1,
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Items per page (1–50, default 20).",
                required=False,
                default=20,
            ),
            ToolParameter(
                name="sort_by",
                type=ParameterType.STRING,
                description=(
                    "Sort field: 'name', 'createdAt', 'updatedAt', 'size', 'type'. For "
                    "knowledge-base files createdAt and updatedAt are upload times, not "
                    "document dates."
                ),
                required=False,
                default="updatedAt",
            ),
            ToolParameter(
                name="sort_order",
                type=ParameterType.STRING,
                description="Sort order: 'asc' or 'desc'.",
                required=False,
                default="desc",
            ),
            ToolParameter(
                name="cursor",
                type=ParameterType.STRING,
                description=(
                    "The next-page token from the selection listed in the prompt. "
                    "Pass it with any query; the cursor chooses the page. "
                    "Omit it to start at the first page."
                ),
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searched Knowledge Graph for "{args.get("query", "")}"'
            if args.get("query") else "Listed files"
        ),
        result_summary=lambda args, result: (
            _list_files_result_summary(args, result)
        ),
    )
    async def list_files(
        self,
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
        """Name-based listing — calls ops/listing.py execute_list_files."""
        from .ops.listing import execute_list_files
        return await execute_list_files(
            self.state,
            query=query,
            cursor=cursor,
            source_ids=source_ids,
            node_types=node_types,
            record_types=record_types,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    # -----------------------------------------------------------------------
    # search_timeline — date-ordered search over a span (TIMELINE_SUMMARIES)
    # -----------------------------------------------------------------------

    @tool(
        path="/tools/knowledgegraph/search_timeline",
        short_description="Search a span of time period by period and return the matches in date order",
        description=(
            "Gather what happened over a span of time, in date order: what came up in a channel, "
            "project or ticket between two dates, how something evolved, what was decided over a "
            "period, where things stand after a run of exchanges.\n\n"
            "It splits the span into up to 6 periods (calendar months, or weeks within one month), "
            "searches each period separately, and returns each period's best-matching blocks under "
            "its own heading, oldest first. A plain search returns the top matches overall, which "
            "bunch around the single best-matching moment and miss the rest of the span; here every "
            "period gets its own share. The whole result is about the size of one search result.\n\n"
            "Pass start and end when the question names a period ('between March and May 2026' -> "
            "start='2026-03-01', end='2026-05-31'), widened by a week or so where it is vague "
            "('late July', 'mid-September'). With no start it covers the year before end, reaching "
            "further back when the best matches are older; pass only start for 'since ...'. "
            "Matches whose record has no source date are listed last. To keep to one channel, "
            "project or space, pass its record_group entityId from "
            "knowledgegraph__search_entities in entity_ids.\n\n"
            "Each period shows a sample, not every record. Answer period by period and cite the "
            "blocks under each period's heading. For a period that matters and looks thin, search "
            "it with knowledgegraph__search and its date filters, or read a record in full."
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="What to look for in every period — the topic, not the dates.",
                required=True,
            ),
            ToolParameter(
                name="start",
                type=ParameterType.STRING,
                description=(
                    "First day of the span, 'YYYY-MM-DD' (start of that day, UTC) or an ISO 8601 "
                    "datetime with a timezone. Omit to cover the year before end, or further back "
                    "to the earliest of the best matches."
                ),
                required=False,
            ),
            ToolParameter(
                name="end",
                type=ParameterType.STRING,
                description=(
                    "Last day of the span, inclusive, same formats as start. Omit to end today."
                ),
                required=False,
            ),
            ToolParameter(
                name="date_field",
                type=ParameterType.STRING,
                description=(
                    "'created' (default) places each record by when it was created at its source: "
                    "a Slack thread by its first message, a ticket by when it was filed. 'modified' "
                    "places it by its last update, for tickets or pages that changed over the span. "
                    "A knowledge-base upload is placed by a date its content states when one was "
                    "found, otherwise by its file's last-modified time or its upload time; the "
                    "result's header says when an upload dated only by its upload time is instead "
                    "listed with no source date."
                ),
                required=False,
                default="created",
            ),
            ToolParameter(
                name="source_ids",
                type=ParameterType.ARRAY,
                description=(
                    "Limit to these sources (app-connector or KB-collection IDs from the Knowledge "
                    "Sources section). Omit to search every accessible source."
                ),
                required=False,
                items={"type": "string"},
            ),
            ToolParameter(
                name="entity_ids",
                type=ParameterType.ARRAY,
                description=(
                    "Optional entityIds returned by search_entities in this turn: a record_group "
                    "(a Slack channel, Jira project, Confluence space, Drive folder) keeps the "
                    "timeline to that group's records; a department/category/topic narrows by it."
                ),
                required=False,
                items={"type": "string"},
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searched "{args.get("query", "")}" period by period'
            + (f' from {args["start"]}' if args.get("start") else "")
            + (f' to {args["end"]}' if args.get("end") else "")
            if args.get("query") else None
        ),
        result_summary=lambda args, result: _timeline_result_summary(args, result),
        display_name="Searched the knowledge base over time",
    )
    async def search_timeline(
        self,
        query: str | None = None,
        start: str | None = None,
        end: str | None = None,
        date_field: str | None = None,
        source_ids: list[str] | None = None,
        entity_ids: list[str] | None = None,
    ) -> str | list[Part]:
        """Date-ordered search — calls ops/timeline.py execute_timeline_search."""
        from .ops.timeline import execute_timeline_search
        return await execute_timeline_search(
            self.state,
            query,
            start=start,
            end=end,
            date_field=date_field,
            source_ids=source_ids,
            entity_ids=entity_ids,
        )

    # -----------------------------------------------------------------------
    # fetch_record — full document read (dynamically granted)
    # -----------------------------------------------------------------------
    # NOTE: this method is declared here for discoverability but the tool is
    # NOT in the initial toolset disclosure — it is registered dynamically by
    # hooks/citations.py once the agent holds at least one Record ID.
    # See ops/fetch.py for the execution logic.


async def _get_user_key(graph_provider: Any, user_id: str) -> str | None:
    """Resolve user_id → internal _key via graph provider."""
    try:
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        if user:
            return user.get("_key") or user.get("id")
        return None
    except Exception as e:
        logger.warning("Failed to get user key for %s: %s", user_id, e)
        return None


# ---------------------------------------------------------------------------
# Summary helpers for the new knowledgegraph tools
# ---------------------------------------------------------------------------

def _search_by_entities_args_summary(args: dict[str, Any]) -> str | None:
    names = [
        str(item.get("name")) for item in args.get("mentions") or []
        if isinstance(item, dict) and item.get("name")
    ]
    return f"Looked up {', '.join(names)}" if names else None


def _search_by_entities_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    if result.is_error:
        return result_text(result.content) or "Entity search failed"
    return search_result_summary(args, result) or "Looked up the named entities"


def _search_entities_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    from app.agents.actions.util.tool_summaries import as_text, parse_json_maybe

    text = as_text(result.content)
    if not text:
        return None
    parsed = parse_json_maybe(text)
    if not isinstance(parsed, dict):
        return "Entity search failed" if result.is_error else None
    if result.is_error or parsed.get("status") == "error":
        return parsed.get("message") or "Entity search failed"
    if isinstance(parsed.get("queries"), list):
        groups = [q for q in parsed["queries"] if isinstance(q, dict)]
        found = sum(len(q.get("results") or []) for q in groups)
        return (
            f"Found {found} entit{'y' if found == 1 else 'ies'} across "
            f"{len(groups)} quer{'y' if len(groups) == 1 else 'ies'}"
        )
    results = parsed.get("results") or []
    if not results:
        return parsed.get("message") or "No matching entities found"
    names = [r.get("name") for r in results[:3] if isinstance(r, dict) and r.get("name")]
    suffix = f" (+{len(results) - 3} more)" if len(results) > 3 else ""
    return f"Found {len(results)} entit{'y' if len(results) == 1 else 'ies'}: " + ", ".join(names) + suffix


def _find_records_by_entity_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    content = result.content or ""
    if not content:
        return None
    text = str(content)
    first_line = text.split("\n", 1)[0]
    if result.is_error:
        return f"Lookup failed: {first_line}"
    return first_line or "Found records"


def _timeline_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    from .ops.timeline import timeline_result_summary
    return timeline_result_summary(args, result)


def _list_files_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    content = result.content or ""
    if not content:
        return None
    first_line = str(content).split("\n", 1)[0] if content else ""
    if "Error:" in str(content):
        return f"Listing failed: {first_line}"
    if "No items found" in str(content):
        return "No items found"
    return first_line or "Listing result"
