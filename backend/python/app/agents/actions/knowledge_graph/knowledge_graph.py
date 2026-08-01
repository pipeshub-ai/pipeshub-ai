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

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.connectors.sources.localKB.handlers.knowledge_hub_service import FOLDER_MIME_TYPES
from app.modules.agents.qna.chat_state import (
    ChatState,
    remember_record_ids,
)

from .catalog import ConnectorCatalog
from .models import NavigationView
from .navigator import GraphNavigator
from .ops.scope import resolve_scope
from .resolver import RecordResolver
from .views import render_lookup_result, render_navigation_view

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult

logger = logging.getLogger(__name__)

_NOT_FOUND_MSG = "Not found or no access."


# ---------------------------------------------------------------------------
# Activity-summary helpers (colocated per project convention)
# ---------------------------------------------------------------------------


def _navigate_args_summary(args: dict[str, Any]) -> str | None:
    node_id = args.get("node_id")
    name_filter = args.get("name_filter")
    page = args.get("page", 1)
    depth = args.get("depth", 1)
    if node_id:
        base = f"Navigated to node {node_id}"
    else:
        base = "Navigated to root"
    if name_filter:
        base += f" (filter: {name_filter!r})"
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


def _time_range_to_kh_filters(
    time_range: dict[str, int] | None,
) -> tuple[dict[str, int | None] | None, dict[str, int | None] | None]:
    """Bridge `ops.time_range.parse_time_range()`'s epoch-ms dict (keyed by
    source_created_after_ms/source_created_before_ms/source_updated_after_ms/
    source_updated_before_ms — the shape knowledgegraph__search's retrieval
    path expects) to the `{"gte": ..., "lte": ...}` shape
    `KnowledgeHubService.get_nodes()` expects for its `created_at`/
    `updated_at` params. Reusing the same parser as search() (rather than a
    second, separate ISO-parsing implementation) means navigate() gets the
    exact same validation: rejecting timezone-naive datetimes, checking
    after <= before, and rejecting future created_after dates.
    """
    if not time_range:
        return None, None
    created_at: dict[str, int | None] | None = None
    if "source_created_after_ms" in time_range or "source_created_before_ms" in time_range:
        created_at = {
            "gte": time_range.get("source_created_after_ms"),
            "lte": time_range.get("source_created_before_ms"),
        }
    updated_at: dict[str, int | None] | None = None
    if "source_updated_after_ms" in time_range or "source_updated_before_ms" in time_range:
        updated_at = {
            "gte": time_range.get("source_updated_after_ms"),
            "lte": time_range.get("source_updated_before_ms"),
        }
    return created_at, updated_at


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
            "Pass name_filter to filter children by name (≥2 chars).\n"
            "Pass node_types to filter children by type (app, recordGroup, record, folder).\n\n"
            "node_id is tolerant: passing a URL or issue key (PA-1787) resolves it automatically.\n\n"
            "Opening a record shows its own metadata — for a ticket that includes status, assignee, "
            "priority and dates — so a question about one record is often answered by this call alone. "
            "For its content, pass the Record ID to knowledgegraph__fetch_record.\n\n"
            "Pass depth=2 or depth=3 to see multiple levels in ONE call instead of navigating one level "
            "at a time — e.g. an epic's stories AND their subtasks. Results are returned as a flat list "
            "sorted by creation date (latest first), with each row showing its level in the hierarchy. "
            "Use this whenever the question needs an overview of a hierarchy rather than a single node "
            "(status of a whole initiative, what a project contains end to end).\n\n"
            "Output always shows:\n"
            "  Path: breadcrumb trail with node IDs\n"
            "  Record ID / Node ID: stable id, pass back to navigate() or to fetch_record()\n"
            "  Children listing with record_id= or node_id= for each item, with a created: date "
            "when known\n"
            "  Related: cross-references (page 1 only, record nodes only)\n"
            "  Next: exactly what to call next\n\n"
            "Time-scoped browsing: pass created_after/created_before or modified_after/modified_before "
            "to filter children by source timestamp (see parameter descriptions for date format) — "
            "use this instead of fetching all children when the question specifies a date range. "
            "Use conservative ranges (pad by a day on each side) since source timestamps may differ "
            "from the user's timezone. If time-filtered results are empty or too few, widen the "
            "range or retry without time filters and filter manually from the listing."
        ),
        parameters=[
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
                name="name_filter",
                type=ParameterType.STRING,
                description="Optional substring filter on child names (≥2 chars).",
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
                description="Children per page (1–100, default 50).",
                required=False,
                default=50,
            ),
            ToolParameter(
                name="depth",
                type=ParameterType.INTEGER,
                description=(
                    "How many levels of children to return in this one call (1-3, default 1). "
                    "depth=2 also fetches each child's own children; depth=3 goes one level "
                    "deeper. Results are returned as a flat list sorted by creation date "
                    "(latest first), with each row showing its level. Only applies on page=1 "
                    "and for record/folder parents. Use depth=2/3 for hierarchy-overview "
                    "questions instead of calling navigate() once per level."
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
            ToolParameter(
                name="node_types",
                type=ParameterType.ARRAY,
                description=(
                    "Filter children by node type. Values: 'app' (connector), "
                    "'recordGroup' (project/space/drive/channel), 'record', 'folder'. "
                    "Omit to show all types. Use when you need only a specific level of "
                    "the hierarchy — e.g. node_types=['record'] to skip record groups, "
                    "or node_types=['recordGroup'] to see only projects/spaces under an app."
                ),
                required=False,
                items={"type": "string"},
            ),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=_navigate_args_summary,
        result_summary=_navigate_result_summary,
    )
    async def navigate(
        self,
        node_id: str | None = None,
        name_filter: str | None = None,
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

        # Normalize
        node_id = node_id.strip() if node_id else None
        name_filter = (name_filter.strip() if name_filter else None) or None
        if name_filter and len(name_filter) < 2:
            name_filter = None
        page = max(1, page)
        limit = min(max(1, limit), 100)
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
        created_at, updated_at = _time_range_to_kh_filters(time_range)

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
        connector_ids = list(scope.app_ids)
        kb_ids = list(scope.kb_ids)

        if not connector_ids and not kb_ids:
            # Chat-mode fallback: use all user-visible connectors
            if not state.get("has_knowledge"):
                return False, "No knowledge sources configured for this agent."
            logger.debug(
                "navigate: empty agent scope — using catalog-wide scope (%d connectors)",
                len(catalog),
            )
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
                    frontend_url=state.get("frontend_url"),
                )
                result = await resolver.resolve_many([node_id])
                if result.matches:
                    node_id = result.matches[0].id

        navigator = GraphNavigator(
            graph_provider=graph_provider,
            user_id=user_id,
            user_key=user_key,
            org_id=org_id,
            frontend_url=state.get("frontend_url"),
        )

        app_names = {c.id: c.name for c in catalog.connectors}

        try:
            view = await navigator.navigate(
                node_id=node_id,
                name_filter=name_filter,
                page=page,
                limit=limit,
                connector_ids=connector_ids or None,
                record_group_ids=list(kb_ids) if kb_ids else None,
                depth=depth,
                created_at=created_at,
                updated_at=updated_at,
                node_types=node_types,
                app_names=app_names,
            )
        except Exception:
            logger.exception("navigate failed for node_id=%s", node_id)
            return False, "Navigation failed — try again or use a different node_id."

        remember_record_ids(state, _record_ids_in_view(view))
        text = render_navigation_view(view, page, record_id_shortener)

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
        connector_ids = list(scope.app_ids)

        resolver = RecordResolver(
            graph_provider=graph_provider,
            catalog=catalog,
            org_id=org_id,
            user_id=user_id,
            user_key=user_key,
            folder_mime_types=FOLDER_MIME_TYPES,
            agent_connector_ids=connector_ids,
            connector_name_hint=connector_name,
            frontend_url=state.get("frontend_url"),
        )

        try:
            result = await resolver.resolve_many(idents)
        except Exception:
            logger.exception("lookup_record resolve_many failed for %s", idents)
            return False, _NOT_FOUND_MSG
        remember_record_ids(state, [m.id for m in result.matches])

        # TEMPORARY token-savings experiment (opt-in, disabled by default —
        # see `ChatQuery.enableRecordIdShortening`) — see `RecordIdShortener`
        # in `utils/chat_helpers.py`. Same shared shortener as navigate()/
        # search()/list_files(), keyed off tool_state so any tool the model
        # calls first mints the shared mapping.
        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        record_id_shortener = get_record_id_shortener_if_enabled(state)

        text = render_lookup_result(result, record_id_shortener)
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
            "per-source recall. Omit source_ids to search all accessible sources in one call.\n\n"
            "Time filtering: use created_after/created_before to scope by source creation date, "
            "and modified_after/modified_before to scope by source last-modified date. "
            "All dates use the source system's timestamp (when the file was created in Drive, "
            "when the Jira ticket was filed), not when it was indexed by PipesHub.\n\n"
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
                    "This filters by the document's original creation date at the source "
                    "(Google Drive, Jira, Confluence, etc.), not when it was indexed. "
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
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searched for "{args.get("query", "")}"'
            + (f' in {len(args.get("source_ids", []))} source(s)'
               if args.get("source_ids") else "")
            if args.get("query") else None
        ),
        result_summary=lambda args, result: (
            _search_result_summary(args, result)
        ),
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
    ) -> str:
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
                description="Sort field: 'name', 'createdAt', 'updatedAt', 'size', 'type'.",
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
    ) -> tuple[bool, str]:
        """Name-based listing — calls ops/listing.py execute_list_files."""
        from .ops.listing import execute_list_files
        return await execute_list_files(
            self.state,
            query=query,
            source_ids=source_ids,
            node_types=node_types,
            record_types=record_types,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
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

def _search_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    import re
    from app.agents.actions.util.tool_summaries import as_text, parse_json_maybe, bullet_list

    text = as_text(result.content)
    if not text:
        return None
    parsed = parse_json_maybe(text)
    if isinstance(parsed, dict) and parsed.get("status") == "error":
        return f"Search failed: {parsed.get('message') or 'Unknown error'}"
    if isinstance(parsed, dict) and (
        parsed.get("result_count") == 0 or (isinstance(parsed.get("results"), list) and not parsed["results"])
    ):
        return str(parsed.get("message") or "No results found")

    match = re.search(r"^Top (\d+) blocks? from (\d+) records?", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        match = re.search(r"^Retrieved (\d+) knowledge blocks? from (\d+) documents?", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    blocks, docs = match.group(1), match.group(2)
    header = f"Retrieved {blocks} block{'s' if blocks != '1' else ''} from {docs} record{'s' if docs != '1' else ''}"
    name_re = re.compile(r"^Name\s*:\s*(.+)$", re.MULTILINE)
    names = [n.strip() for n in name_re.findall(text) if n.strip()]
    if not names:
        return header
    return header + "\n" + bullet_list(names)


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
