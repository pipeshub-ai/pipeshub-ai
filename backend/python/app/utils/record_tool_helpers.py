"""Shared helpers for the graph-navigation agent tools (``lookup_record``, ``navigate``).

Provides the small set of cross-cutting pieces both tools need so neither one
re-implements permission checks, connector resolution, ref minting, or
markdown rendering:

- ``check_record_access``       — permission-gated record fetch (mirrors the
  check-then-fetch pattern already used by ``fetch_full_record``).
- ``resolve_org_connector_ids`` — org-scoped connector/app resolution; never
  trusts a raw connector ID from the caller.
- ``NodeRefMapper``             — short per-conversation node refs (``n1``,
  ``n2``, ...), the token-saving analog of ``CitationRefMapper``.
- ``build_pagination``          — Knowledge Hub ``PaginationInfo`` envelope.
- ``render_node_view_markdown`` — single markdown renderer shared by both
  tools' "page" output.
- A tiny tool-describer registry consumed by ``streaming.py`` so SSE
  ``tool_call`` events can show a human-readable activity string without
  ``streaming.py`` importing individual tool modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.logger import create_logger

logger = create_logger("record_tool_helpers")

NOT_FOUND_ERROR = "Record not found or not accessible"


async def check_record_access(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    record_id: str,
) -> dict[str, Any] | None:
    """Permission-gate a record, then fetch its full document.

    ``check_record_access_with_details`` is used purely as a boolean
    permission gate here (its return shape is a provider-internal
    permission-path trace, not display data) — the same pattern
    ``fetch_full_record`` already uses. The record's display fields are
    fetched separately via ``get_document``.

    Returns ``None`` uniformly whether the record is missing OR the caller
    lacks access — callers must never use this to distinguish the two cases
    (that would leak record existence to unauthorized users).
    """
    if not (graph_provider and user_id and org_id and record_id):
        return None
    try:
        access = await graph_provider.check_record_access_with_details(user_id, org_id, record_id)
    except Exception:
        logger.exception("check_record_access: permission check failed for record_id=%s", record_id)
        return None
    if not access:
        return None
    try:
        doc = await graph_provider.get_document(record_id, CollectionNames.RECORDS.value)
    except Exception:
        logger.exception("check_record_access: get_document failed for record_id=%s", record_id)
        return None
    return doc


async def resolve_org_connector_ids(
    graph_provider: IGraphDBProvider,
    org_id: str,
    connector_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return org-owned app/connector docs, optionally narrowed by a connector-family hint.

    Never accepts a raw connector ID from the caller — this only ever
    returns apps the org actually owns, so a ``connector_name`` filter can
    narrow the search but can never widen it into another org's connectors.
    An unmatched hint falls back to searching all of the org's connectors
    rather than returning nothing (the hint is advisory, not a hard filter).
    """
    try:
        apps = await graph_provider.get_org_apps(org_id)
    except Exception:
        logger.exception("resolve_org_connector_ids: get_org_apps failed for org_id=%s", org_id)
        return []
    if not apps:
        return []
    if not connector_name:
        return apps

    hint = connector_name.strip().upper()
    if not hint:
        return apps

    matched = [
        app for app in apps
        if hint in (app.get("type") or "").upper() or hint in (app.get("name") or "").upper()
    ]
    return matched or apps


async def resolve_user_key(graph_provider: IGraphDBProvider, user_id: str) -> str | None:
    """Resolve an auth ``userId`` to the internal User node ``_key`` used by permission edges."""
    try:
        user = await graph_provider.get_user_by_user_id(user_id)
    except Exception:
        logger.exception("resolve_user_key: lookup failed for user_id=%s", user_id)
        return None
    return user.get("_key") if user else None


def truncate_name(name: str | None, max_len: int = 80) -> str:
    """Truncate a display name to keep listings token-cheap."""
    if not name:
        return "(untitled)"
    name = str(name).strip()
    if not name:
        return "(untitled)"
    return name if len(name) <= max_len else name[: max_len - 1].rstrip() + "…"


@dataclass
class NodeRefMapper:
    """Per-conversation short refs (``n1``, ``n2``, ...) for graph nodes.

    Token-saving analog of ``CitationRefMapper``: a 20-item listing drops
    from ~720 UUID chars to ~60. ``resolve()`` accepts either a minted ref or
    a raw UUID so a model that forgets to use the short ref (or copies a
    UUID straight out of earlier context) still works.
    """

    _counter: int = field(default=0)
    _id_to_ref: dict[str, str] = field(default_factory=dict)
    _ref_to_id: dict[str, str] = field(default_factory=dict)

    def get_or_create_ref(self, node_id: str | None) -> str:
        if not node_id:
            return ""
        if node_id in self._id_to_ref:
            return self._id_to_ref[node_id]
        self._counter += 1
        ref = f"n{self._counter}"
        self._id_to_ref[node_id] = ref
        self._ref_to_id[ref] = node_id
        return ref

    def resolve(self, ref_or_id: str | None) -> str | None:
        """Return the underlying node ID for a ref, or the input unchanged if it's already an ID."""
        if not ref_or_id:
            return ref_or_id
        return self._ref_to_id.get(ref_or_id, ref_or_id)

    @property
    def ref_to_id(self) -> dict[str, str]:
        return dict(self._ref_to_id)


def build_pagination(page: int, limit: int, total_items: int) -> dict[str, Any]:
    """Knowledge Hub ``PaginationInfo`` shape, usable standalone outside KnowledgeHubService."""
    total_pages = (total_items + limit - 1) // limit if total_items > 0 else 0
    return {
        "page": page,
        "limit": limit,
        "totalItems": total_items,
        "totalPages": total_pages,
        "hasNext": page < total_pages,
        "hasPrev": page > 1,
    }


def render_node_view_markdown(view: dict[str, Any]) -> str:
    """Render a single "page" for the navigate/lookup tools.

    Expected ``view`` keys (all optional except ``items``):
    - ``current``: {"ref", "id", "type", "name", "webUrl", "status", "connector"}
    - ``breadcrumb_path``: str, e.g. "Jira › Payments Project › PA-1787"
    - ``items``: list of {"ref", "type", "name", "detail", "expandable"}
    - ``items_heading``: str, default "Contents"
    - ``related``: list of {"ref", "type", "name", "detail"}
    - ``pagination``: PaginationInfo-shaped dict
    - ``is_delta``: bool — when True (page > 1), skip header/breadcrumbs/related
      so continuation pages cost only the items themselves.
    """
    lines: list[str] = []
    current = view.get("current")
    is_delta = bool(view.get("is_delta"))

    if current and not is_delta:
        type_label = current.get("type") or "NODE"
        header = f"# [{type_label}] {current.get('name') or '(untitled)'}"
        if current.get("ref"):
            header += f" {{{current['ref']}}}"
        lines.append(header)
        if view.get("breadcrumb_path"):
            lines.append(f"Path: {view['breadcrumb_path']}")
        meta_bits = []
        if current.get("webUrl"):
            meta_bits.append(f"URL: {current['webUrl']}")
        if current.get("status"):
            meta_bits.append(f"Status: {current['status']}")
        if current.get("connector"):
            meta_bits.append(f"Connector: {current['connector']}")
        if meta_bits:
            lines.append(" | ".join(meta_bits))
        lines.append("")
    elif view.get("breadcrumb_path") and not is_delta:
        lines.append(f"Path: {view['breadcrumb_path']}")
        lines.append("")

    items = view.get("items") or []
    pagination = view.get("pagination")
    heading = view.get("items_heading") or "Contents"
    if pagination:
        total_pages = max(pagination.get("totalPages", 1), 1)
        page_bit = f"page {pagination.get('page', 1)}/{total_pages}"
        count_bit = f"{len(items)} of {pagination.get('totalItems', len(items))}"
        lines.append(f"## {heading} ({page_bit} — {count_bit})")
    else:
        lines.append(f"## {heading}")

    if items:
        for item in items:
            marker = " ▸" if item.get("expandable") else ""
            detail = f" — {item['detail']}" if item.get("detail") else ""
            lines.append(
                f"- {item.get('ref', '')} [{item.get('type', 'ITEM')}] "
                f"{item.get('name', '(untitled)')}{marker}{detail}"
            )
    else:
        lines.append("(empty)")

    related = view.get("related")
    if related and not is_delta:
        lines.append("")
        lines.append("## Related")
        for item in related:
            detail = f" ({item['detail']})" if item.get("detail") else ""
            lines.append(f"- {item.get('ref', '')} [{item.get('type', 'ITEM')}] {item.get('name', '(untitled)')}{detail}")

    hints: list[str] = []
    if pagination and pagination.get("hasNext"):
        next_page = pagination.get("page", 1) + 1
        if current and current.get("ref"):
            hints.append(f'More: navigate(node_id="{current["ref"]}", page={next_page})')
        else:
            hints.append(f'More: navigate(page={next_page})')
    if current and current.get("id"):
        hints.append(f'Read: fetch_full_record(record_ids=["{current["id"]}"])')
    if hints:
        lines.append("")
        lines.append(" · ".join(hints))

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tool-describer registry — consumed by streaming.py to emit human-readable
# `tool_call` SSE events without streaming.py needing to know about
# individual tool modules (new tools self-register on import).
# --------------------------------------------------------------------------
_TOOL_DESCRIBERS: dict[str, Callable[[dict[str, Any]], str]] = {}


def register_tool_describer(tool_name: str, describer: Callable[[dict[str, Any]], str]) -> None:
    """Register a ``function(args) -> str`` used to describe a tool's ``tool_call`` SSE event."""
    _TOOL_DESCRIBERS[tool_name] = describer


def describe_tool_call(tool_name: str, args: dict[str, Any] | None) -> str:
    """Return a human-readable activity string for a tool call, e.g. "Looking up PA-1787 in Jira…"."""
    describer = _TOOL_DESCRIBERS.get(tool_name)
    if describer:
        try:
            described = describer(args or {})
            if described:
                return described
        except Exception:
            logger.debug("describe_tool_call: describer failed for %s", tool_name, exc_info=True)
    return f"Using {tool_name}…"
