"""KnowledgeScope — the single, authoritative scope resolver.

Replaces three divergent inline scope-derivation blocks:
  - knowledge_graph._get_scoping (lines 147-176)
  - knowledge_hub.list_files     (lines 301-347)
  - retrieval filter-group builder (lines 288-362)

All three checked the same three priority sources in the same order but
each differed in return type, sentinel handling, and catalog fallback.
This module is the canonical implementation; call sites import
``resolve_scope`` and delegate entirely.

Security invariant (unchanged from each original):
  The returned scope is always a *subset* of what the agent's configured
  knowledge grants.  ``narrow_to`` never widens — if the caller passes ids
  not in scope the method falls back to the full agent scope rather than
  expanding to org-wide records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Sequence

from app.services.graph_db.interface.graph_db_provider import (
    ALLOWED_FILTER_KEYS,
    SELECTION_APPS_FILTER_KEY,
    SELECTION_FILTER_KEYS,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_NO_KB_SENTINEL = "NO_KB_SELECTED"


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeScope:
    """Immutable, validated agent scope resolved from ChatState."""

    app_ids: tuple[str, ...]
    kb_ids: tuple[str, ...]
    #: Filter keys carried to the retrieval service as they came: a selection
    #: below app level, and an agent's or project's allow-list.
    passthrough: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def has_selection(self) -> bool:
        return any(key in self.passthrough for key in SELECTION_FILTER_KEYS)

    def is_empty(self) -> bool:
        return not self.app_ids and not self.kb_ids and not self.has_selection

    def narrow_to(self, requested: Sequence[str] | None) -> "KnowledgeScope":
        """Return a scope restricted to *requested* ids.

        Rules:
        - ``None`` / empty → return self (no narrowing requested).
        - Intersection non-empty → return that intersection.
        - Intersection empty (hallucinated ids) → return self as fallback
          so the search space is never accidentally empty.
        """
        # A selection below app level is the scope as the user set it: its
        # nodes are not known per app here, so it is not narrowed by app.
        if not requested or self.has_selection:
            return self
        requested_set = frozenset(requested)
        new_apps = tuple(a for a in self.app_ids if a in requested_set)
        new_kbs = tuple(k for k in self.kb_ids if k in requested_set)
        if not new_apps and not new_kbs:
            return self
        return KnowledgeScope(app_ids=new_apps, kb_ids=new_kbs, passthrough=self.passthrough)

    def to_filter_groups(self) -> dict[str, list[str]]:
        """Build the ``filter_groups`` dict the retrieval service expects.

        Empty KB list means ``[]`` — no KB restriction.  The ``NO_KB_SELECTED``
        sentinel is only used in single-source fan-out calls (see
        ``to_filter_groups_for_source``), where scoping to one app must
        explicitly exclude KB results.
        """
        return {
            "apps": list(self.app_ids),
            "kb": list(self.kb_ids),
            **self._passthrough_groups(),
        }

    def _passthrough_groups(self) -> dict[str, list[str]]:
        return {key: list(ids) for key, ids in self.passthrough.items()}

    def to_filter_groups_for_source(
        self,
        app_id: str | None = None,
        kb_id: str | None = None,
        *,
        placeholder_agent: bool = False,
    ) -> dict[str, list[str]]:
        """Single-source filter group for fan-out searches.

        When isolating one app, use ``NO_KB_SELECTED`` so the retrieval
        service does not mix in KB results (unless this is a placeholder agent,
        which has no curated KB filter and should not restrict the KB side).
        """
        no_kb = [] if placeholder_agent else [_NO_KB_SENTINEL]
        if app_id:
            return {"apps": [app_id], "kb": no_kb, **self._passthrough_groups()}
        if kb_id:
            return {"apps": [], "kb": [kb_id], **self._passthrough_groups()}
        return self.to_filter_groups()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _clean_kb(raw: list[str]) -> tuple[str, ...]:
    return tuple(k for k in raw if k and k != _NO_KB_SENTINEL)


def _has_selection(filters: Mapping[str, object]) -> bool:
    return any(filters.get(key) for key in SELECTION_FILTER_KEYS)


def search_scope(state: "ChatState") -> KnowledgeScope:
    """The scope a content search runs in.

    The turn's curated filters, with a selection below app level and an
    allow-list carried along. The universal agent's filters are synthetic, so
    without a selection it searches its configured sources instead.
    """
    filters: dict = state.get("filters", {}) or {}
    passthrough = {
        key: tuple(filters[key])
        for key in (*SELECTION_FILTER_KEYS, SELECTION_APPS_FILTER_KEY)
        if filters.get(key)
    }
    # A bound that lists nothing still bounds: it admits nothing.
    passthrough.update({key: tuple(filters[key] or ()) for key in ALLOWED_FILTER_KEYS if key in filters})
    if state.get("is_placeholder_agent", False) and not _has_selection(filters):
        apps, kbs = state.get("apps") or [], state.get("kb") or []
    else:
        apps, kbs = filters.get("apps") or [], filters.get("kb") or []
    return KnowledgeScope(app_ids=tuple(apps), kb_ids=_clean_kb(list(kbs)), passthrough=passthrough)


def derive_scope(state: "ChatState") -> KnowledgeScope:
    """Synchronous scope derivation — priorities 1-3 only (no I/O).

    Used by navigate() and lookup_record() which are already inside async
    methods and need only the cached state data.  list_files() and the
    retrieval builder use the async ``resolve_scope`` when they need the
    catalog fallback.

    This is the per-app view. With a selection below app level it is the apps
    the selection touches; a content search uses ``search_scope`` instead.
    """
    from app.modules.agents.qna.chat_state import (
        _extract_kb_app_ids,
        _extract_knowledge_connector_ids,
    )

    turn_filters: dict = state.get("filters") or {}
    if _has_selection(turn_filters):
        touched = [*(turn_filters.get("apps") or []), *(turn_filters.get(SELECTION_APPS_FILTER_KEY) or [])]
        return KnowledgeScope(
            app_ids=tuple(dict.fromkeys(touched)),
            kb_ids=_clean_kb(list(turn_filters.get("kb") or [])),
        )

    app_ids: list[str] = list(state.get("apps") or [])
    raw_kb: list[str] = list(state.get("kb") or [])
    kb_ids: list[str] = list(_clean_kb(raw_kb))

    if not app_ids or not kb_ids:
        filters: dict = state.get("filters") or {}
        if not app_ids:
            app_ids = list(filters.get("apps") or [])
        if not kb_ids:
            kb_ids = list(_clean_kb(filters.get("kb") or []))

    if not app_ids and not kb_ids:
        knowledge = state.get("agent_knowledge") or []
        app_ids = _extract_knowledge_connector_ids(knowledge)
        kb_ids = _extract_kb_app_ids(knowledge)

    return KnowledgeScope(app_ids=tuple(app_ids), kb_ids=tuple(kb_ids))


def apps_usable_whole(state: "ChatState", app_ids: Sequence[str]) -> list[str]:
    """Of ``app_ids``, the apps a tool may work on as a whole.

    The entity tools work per app and name records anywhere in it. So an app
    is left out when the turn only reaches part of it: an app a selection
    below app level merely touches, or one a bound (a saved agent's sources, a
    project's) limits to some of its nodes. Without a selection or a bound
    every app is kept.
    """
    from app.modules.retrieval.selection_scope import allowed_filters

    filters = state.get("filters") or {}
    kept = list(app_ids)
    if _has_selection(filters):
        selected_whole = {*(filters.get("apps") or []), *(filters.get("kb") or [])}
        kept = [app_id for app_id in kept if app_id in selected_whole]
    for bound in allowed_filters(filters):
        whole = set(bound["apps"])
        kept = [app_id for app_id in kept if app_id in whole]
    return kept


async def ids_within_scope(
    state: "ChatState",
    graph_provider: "IGraphDBProvider",
    user_key: str,
    org_id: str,
    node_ids: Sequence[str],
) -> set[str]:
    """Of ``node_ids``, those the turn may read: inside the selection below
    app level and inside every bound. All of them for a turn with neither.

    For the tools that reach a record by name or by id, which no vector
    filter narrows. A file the user attached to this conversation is always
    readable: it is part of the question, not of the sources.
    """
    from app.modules.retrieval.selection_scope import resolve_request_scopes

    filters = state.get("filters") or {}
    ids = [node_id for node_id in dict.fromkeys(node_ids) if node_id]
    scoped = _has_selection(filters) or any(key in filters for key in ALLOWED_FILTER_KEYS)
    if not scoped or not ids:
        return set(ids)
    attached = {
        attachment.get("recordId")
        for attachment in state.get("attachments") or []
        if isinstance(attachment, dict)
    }
    _, scopes = await resolve_request_scopes(graph_provider, user_key, org_id, filters)
    access = await graph_provider.check_access(user_key, org_id, node_ids=ids, scopes=scopes)
    return set(access.node_ids_in_scope) | (attached & set(ids))


async def resolve_scope(
    state: "ChatState",
    *,
    allow_catalog_fallback: bool = False,
) -> KnowledgeScope:
    """Derive the agent's knowledge scope from ChatState.

    Priority order (same as each original implementation):
    1. ``state["apps"]`` / ``state["kb"]`` — pre-extracted by create_chat_state.
    2. ``state["filters"]["apps"]`` / ``state["filters"]["kb"]`` — route filter.
    3. Re-derive from ``state["agent_knowledge"]`` (legacy fallback).
    4. If ``allow_catalog_fallback`` and still empty: query the ConnectorCatalog
       (the path knowledge_hub.list_files took in chat-mode; navigate/search do
       not need it because they handle the empty-scope case differently).

    Returns an empty ``KnowledgeScope`` for chat-mode agents that have no
    per-agent knowledge configured.  Callers decide what "empty" means for them.
    """
    scope = derive_scope(state)
    # A selection below app level that touches no app must stay empty.
    if scope.is_empty() and allow_catalog_fallback and not _has_selection(state.get("filters") or {}):
        catalog_scope = await _catalog_fallback(state)
        if catalog_scope is not None:
            return catalog_scope
    return scope


async def _catalog_fallback(state: "ChatState") -> KnowledgeScope | None:
    """Last-resort: derive scope from the ConnectorCatalog for chat-mode agents."""
    if not state.get("has_knowledge"):
        return None
    graph_provider = state.get("graph_provider")
    if not graph_provider:
        return None
    try:
        from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog

        user_id = state.get("user_id", "")
        org_id = state.get("org_id", "")
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        user_key = (user.get("_key") or user.get("id")) if user else None
        if not user_key:
            return None
        catalog = await ConnectorCatalog.build(
            state,
            graph_provider=graph_provider,
            user_key=user_key,
            org_id=org_id,
        )
        app_ids = tuple(catalog.connector_ids())
        return KnowledgeScope(app_ids=app_ids, kb_ids=())
    except Exception as err:
        logger.warning("resolve_scope: catalog fallback failed: %s", err)
        return None


# ---------------------------------------------------------------------------
# Browsing under a selection below app level
# ---------------------------------------------------------------------------

# A conversation is limited by what the user selected or by what a saved agent
# or a project may search; the wording fits both.
SELECTION_BROWSE_HINT = (
    "This conversation is limited to specific folders or items. Call "
    "knowledgegraph__list_files to see them, then pass one of their ids as node_id."
)
SELECTION_OUTSIDE_MESSAGE = (
    "That item is outside what this conversation is limited to."
)
SELECTION_ALONE_MESSAGE = (
    "That item is part of this conversation on its own; what is under it is not."
)
_SELECTED_KINDS = {"apps": "app", "kb": "collection", "recordGroups": "record group"}


async def selection_browse_refusal(
    state: "ChatState", graph_provider: object, user_key: str, org_id: str, node_id: str | None,
) -> str | None:
    """Why a browse of ``node_id`` is refused while the turn's selection is
    below app level, or None when it may go ahead. Only a node inside the
    selection may be browsed; without a node there is no root to list; and a
    record selected alone has nothing under it that belongs to the turn."""
    filters: dict = state.get("filters") or {}
    if not _has_selection(filters):
        return None
    if not node_id:
        return SELECTION_BROWSE_HINT
    from app.modules.retrieval.selection_scope import resolve_request_scopes

    async def inside(turn_filters: dict) -> bool:
        _, scopes = await resolve_request_scopes(graph_provider, user_key, org_id, turn_filters)
        check = await graph_provider.check_access(user_key, org_id, node_ids=[node_id], scopes=scopes)
        return node_id in check.node_ids_in_scope

    if not await inside(filters):
        return SELECTION_OUTSIDE_MESSAGE
    if node_id in (filters.get("recordsExact") or []):
        # Still browsable when something else selected covers it with its subtree.
        others = {key: value for key, value in filters.items() if key != "recordsExact"}
        covered = any(others.get(key) for key in ("apps", "kb", "recordGroups", "records"))
        if not (covered and await inside(others)):
            return SELECTION_ALONE_MESSAGE
    return None


async def list_selected_nodes(
    state: "ChatState", graph_provider: object, user_id: str, org_id: str,
) -> str | None:
    """The turn's selection as a listing, or None when it is not below app
    level. Each line names one selected node the user can read and that a
    saved agent's and a project's sources admit."""
    from app.config.constants.arangodb import FOLDER_MIME_TYPES, CollectionNames
    from app.modules.retrieval.selection_scope import resolve_request_scopes

    filters: dict = state.get("filters") or {}
    if not _has_selection(filters):
        return None
    user = await graph_provider.get_user_by_user_id(user_id=user_id)
    user_key = ((user.get("_key") or user.get("id")) if user else None) or ""
    selected = {
        key: [i for i in filters.get(key) or [] if isinstance(i, str) and i and i != _NO_KB_SENTINEL]
        for key in ("apps", "kb", *SELECTION_FILTER_KEYS)
    }
    bound_filters = {key: filters[key] for key in ALLOWED_FILTER_KEYS if key in filters}
    _, bounds = await resolve_request_scopes(graph_provider, user_key, org_id, bound_filters)
    access = await graph_provider.check_access(
        user_key, org_id, node_ids=[i for ids in selected.values() for i in ids], scopes=bounds,
    )
    readable = access.node_ids_in_scope if bounds else access.node_ids

    async def _names(collection: str, ids: list[str], fields: list[str]) -> dict[str, dict]:
        ids = [i for i in ids if i in readable]
        if not ids:
            return {}
        rows = await graph_provider.get_nodes_by_field_in(collection, "id", ids, return_fields=["id", *fields])
        return {(row.get("id") or row.get("_key")): row for row in rows or []}

    apps = await _names(CollectionNames.APPS.value, [*selected["apps"], *selected["kb"]], ["name"])
    groups = await _names(CollectionNames.RECORD_GROUPS.value, selected["recordGroups"], ["groupName"])
    records = await _names(
        CollectionNames.RECORDS.value, [*selected["records"], *selected["recordsExact"]],
        ["recordName", "mimeType"],
    )
    lines: list[str] = []
    for key, ids in selected.items():
        for node_id in ids:
            if key in ("apps", "kb") and node_id in apps:
                lines.append(f"- {apps[node_id].get('name') or node_id} [{_SELECTED_KINDS[key]}] id={node_id}")
            elif key == "recordGroups" and node_id in groups:
                lines.append(f"- {groups[node_id].get('groupName') or node_id} [record group] id={node_id}")
            elif key in ("records", "recordsExact") and node_id in records:
                row = records[node_id]
                kind = "folder" if row.get("mimeType") in FOLDER_MIME_TYPES else "record"
                alone = " (this item only, nothing under it)" if key == "recordsExact" else ""
                lines.append(f"- {row.get('recordName') or node_id} [{kind}] id={node_id}{alone}")
    if not lines:
        return "Nothing this conversation is limited to is available."
    return "\n".join([
        "This conversation is limited to these items:",
        *lines,
        "Pass one of these ids to knowledgegraph__navigate to see what is inside it, "
        "or use knowledgegraph__search to search inside all of them.",
    ])
