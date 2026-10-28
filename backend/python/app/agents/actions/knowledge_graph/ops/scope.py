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

import base64
import binascii
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Sequence

from app.services.graph_db.interface.graph_db_provider import (
    ALLOWED_FILTER_KEYS,
    SELECTION_APPS_FILTER_KEY,
    SELECTION_FILTER_KEYS,
    STRICT_SCOPE_FILTER_KEY,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_NO_KB_SENTINEL = "NO_KB_SELECTED"
#: The current turn's readable attachments as virtual record ids, which chat
#: mode also adds to ``filters["kb"]`` so search covers them.
ATTACHMENT_RECORD_IDS_KEY = "attachment_record_ids"


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


def browsable_app_ids(state: "ChatState", scope: KnowledgeScope) -> list[str]:
    """The scope's whole apps a listing may filter by connector id: connectors
    and knowledge bases alike, since a knowledge base is an app whose id is its
    records' ``connectorId``. A chat-mode turn adds its attachments' virtual
    record ids to ``kb``; they are not apps, and as a connector filter they
    would hide everything."""
    attached = set(state.get(ATTACHMENT_RECORD_IDS_KEY) or ())
    return [i for i in dict.fromkeys([*scope.app_ids, *scope.kb_ids]) if i not in attached]


def turn_picked_sources(state: "ChatState") -> bool:
    """Whether the turn is limited to sources the user picked (or a project's,
    under ``strictScope``), not counting the attachments chat mode adds."""
    return _turn_picked_apps(state) is not None


def _clean_kb(raw: list[str]) -> tuple[str, ...]:
    return tuple(k for k in raw if k and k != _NO_KB_SENTINEL)


def _has_selection(filters: Mapping[str, object]) -> bool:
    return any(filters.get(key) for key in SELECTION_FILTER_KEYS)


def _navigation_is_limited(state: "Mapping[str, object]") -> bool:
    """Whether a navigate answer has to stay inside a selection, a picked app,
    or a saved agent's or a project's sources."""
    filters = state.get("filters") or {}
    return (
        _has_selection(filters)
        or _turn_picked_apps(state) is not None
        or any(key in filters for key in ALLOWED_FILTER_KEYS)
    )


def _turn_picked_apps(state: "Mapping[str, object]") -> frozenset[str] | None:
    """``_picked_apps`` without the attachments chat mode adds to ``kb``: they
    are part of the question, and as picked apps they would hide every source.
    A project's strict scope keeps what it names."""
    filters = state.get("filters") or {}
    picked = _picked_apps(filters)
    if picked is None or filters.get(STRICT_SCOPE_FILTER_KEY):
        return picked
    return (picked - set(state.get(ATTACHMENT_RECORD_IDS_KEY) or ())) or None


def _picked_apps(filters: Mapping[str, object]) -> frozenset[str] | None:
    """The whole apps a turn without a selection is limited to, as search is:
    ``apps`` and ``kb`` (a project's apps narrowed by the picker), where an
    empty list under ``strictScope`` means none. None when nothing was picked."""
    if _has_selection(filters):
        return None
    picked = frozenset((*(i for i in filters.get("apps") or () if i), *_clean_kb(list(filters.get("kb") or ()))))
    return picked if picked or filters.get(STRICT_SCOPE_FILTER_KEY) else None


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


async def ids_within_scope(
    state: "ChatState",
    graph_provider: "IGraphDBProvider",
    user_key: str,
    org_id: str,
    node_ids: Sequence[str],
) -> set[str]:
    """Of ``node_ids``, those the turn may read: inside the selection below
    app level, or the whole apps picked without one, and inside every bound.
    All of them for a turn with none of these.

    For the tools that reach a record by name or by id, which no vector
    filter narrows. A file the user attached to this conversation, in this
    turn or an earlier one, is always readable: it is part of the question,
    not of the sources.
    """
    from app.config.constants.arangodb import CollectionNames
    from app.modules.retrieval.selection_scope import (
        SelectionScope,
        resolve_request_scopes,
    )
    from app.utils.attachment_utils import is_chat_attachment

    filters = state.get("filters") or {}
    ids = [node_id for node_id in dict.fromkeys(node_ids) if node_id]
    picked = _turn_picked_apps(state)
    scoped = _has_selection(filters) or picked is not None or any(key in filters for key in ALLOWED_FILTER_KEYS)
    if not scoped or not ids:
        return set(ids)
    earlier = [
        attachment
        for turn in state.get("previous_conversations") or [] if isinstance(turn, dict)
        for attachment in turn.get("attachments") or []
    ]
    attached = {
        attachment.get("recordId")
        for attachment in [*(state.get("attachments") or []), *earlier]
        if isinstance(attachment, dict)
    } & set(ids)
    if attached:
        # The client names an attachment's recordId: only a real chat upload of
        # this org passes the scope as an attachment.
        rows = await graph_provider.get_nodes_by_field_in(
            CollectionNames.RECORDS.value, "id", sorted(attached),
            return_fields=["id", "orgId", "origin", "connectorId"], raise_on_error=True,
        )
        attached = {row.get("id") or row.get("_key") for row in rows or [] if is_chat_attachment(row, org_id)}
    _, scopes = await resolve_request_scopes(graph_provider, user_key, org_id, filters)
    if picked is not None:
        scopes = [*scopes, SelectionScope(app_ids=picked)]
    access = await graph_provider.check_access(user_key, org_id, node_ids=ids, scopes=scopes)
    return set(access.node_ids_in_scope) | attached


async def ids_the_user_can_read(
    graph_provider: "IGraphDBProvider",
    user_key: str,
    org_id: str,
    node_ids: Sequence[str],
) -> set[str]:
    """Of ``node_ids``, those ``check_access`` says this user can read.

    A turn that is limited uses ``ids_within_scope`` instead: that check
    applies the limit as well as this one.
    """
    ids = [node_id for node_id in dict.fromkeys(node_ids) if node_id]
    if not ids:
        return set()
    access = await graph_provider.check_access(user_key, org_id, node_ids=ids)
    return set(access.node_ids)


async def navigation_nameable_ids(
    state: "ChatState",
    graph_provider: "IGraphDBProvider",
    user_key: str,
    org_id: str,
    *,
    ancestor_ids: Sequence[str],
    row_ids: Sequence[str],
    related_ids: Sequence[str],
) -> tuple[set[str] | None, set[str], set[str]]:
    """Ancestor ids, child ids, and related ids a navigate answer may name.

    Ancestors are ``None`` when the turn has no limit: the listing's trail
    already keeps only ancestors this user can open, and cutting it would
    hide where the node sits. A limit cuts the trail at that limit, so a
    parent outside it is not named and the sibling hint cannot point at it.
    Children are always checked. The listing is not told the conversation's
    limit, and a child the user cannot read must not be offered to fetch.
    """
    if _navigation_is_limited(state):
        asked = list(dict.fromkeys([*ancestor_ids, *row_ids, *related_ids]))
        allowed = await ids_within_scope(state, graph_provider, user_key, org_id, asked) if asked else set()
        return allowed, allowed, allowed
    readable = await ids_the_user_can_read(graph_provider, user_key, org_id, row_ids)
    return None, readable, {node_id for node_id in related_ids if node_id}


async def ids_outside_scope(
    state: "Mapping[str, object]",
    graph_provider: "IGraphDBProvider",
    user_id: str,
    org_id: str,
    record_ids: Sequence[str],
) -> list[str]:
    """Of ``record_ids``, those the turn may not read: outside the selection
    below app level, or outside a saved agent's or a project's sources. None
    for a turn with neither, without asking the graph."""
    filters = state.get("filters") or {}
    scoped = (
        _has_selection(filters) or _turn_picked_apps(state) is not None
        or any(key in filters for key in ALLOWED_FILTER_KEYS)
    )
    if not scoped:
        return []
    user = await graph_provider.get_user_by_user_id(user_id=user_id)
    user_key = (user or {}).get("_key") or (user or {}).get("id")
    if not user_key:
        return list(record_ids)
    inside = await ids_within_scope(state, graph_provider, user_key, org_id, record_ids)
    return [record_id for record_id in record_ids if record_id not in inside]


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
    record selected alone has nothing under it that belongs to the turn.
    Without a selection a named node must still be inside the picked apps and
    every bound, as for a read by id."""
    filters: dict = state.get("filters") or {}
    if not _has_selection(filters):
        if node_id and node_id not in await ids_within_scope(state, graph_provider, user_key, org_id, [node_id]):
            return SELECTION_OUTSIDE_MESSAGE
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


# The chat UI and the request validator both stop at 200 selected ids
# (`MAX_SELECTED_NODES`, `MAX_FILTER_IDS`). The page is that same cap, so a
# selection the product accepts fits in the prompt and a next page is rare.
# MCP list pagination leaves the page size to the server.
SELECTED_NODE_PAGE_SIZE = 200


def _selection_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _selection_offset(cursor: str) -> int:
    padding = "=" * (-len(cursor) % 4)
    try:
        offset = int(base64.urlsafe_b64decode(cursor + padding))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("unreadable selection cursor") from exc
    if offset < 0:
        raise ValueError("unreadable selection cursor")
    return offset


async def list_selected_nodes(
    state: "ChatState", graph_provider: object, user_id: str, org_id: str,
    *,
    cursor: str | None = None,
    page_size: int = SELECTED_NODE_PAGE_SIZE,
) -> str | None:
    """The turn's selection as a listing, or None when it is not below app
    level. Each line names one selected node the user can read and that a
    saved agent's and a project's sources admit."""
    from app.modules.retrieval.selection_scope import (
        readable_named_nodes,
        resolve_request_scopes,
    )

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
    lines: list[str] = []
    for named in await readable_named_nodes(graph_provider, user_key, org_id, selected, bounds):
        kind = _SELECTED_KINDS.get(named.key) or named.node_type
        alone = " (this item only, nothing under it)" if named.key == "recordsExact" else ""
        lines.append(f"- {named.name} [{kind}] id={named.id}{alone}")
    if not lines:
        return "Nothing this conversation is limited to is available."
    page_size = max(1, page_size)
    try:
        start = _selection_offset(cursor) if cursor else 0
    except ValueError:
        return (
            "That cursor is not a page of this selection. "
            "Call knowledgegraph__list_files without a cursor to start again."
        )
    page = lines[start:start + page_size]
    if cursor and not page:
        return "There is no further page of this selection."
    nxt = start + len(page)
    more = _selection_cursor(nxt) if nxt < len(lines) else None
    hint = (
        "Pass one of these ids to knowledgegraph__navigate to see what is inside it, "
        "or use knowledgegraph__search to search inside all of them."
    )
    if more or cursor:
        header = (
            f"This conversation is limited to these items "
            f"(showing {start + 1}–{nxt} of {len(lines)}):"
        )
    else:
        header = "This conversation is limited to these items:"
    parts = [header, *page, hint]
    if more:
        parts.append(
            f"More items follow. Call knowledgegraph__list_files with cursor=\"{more}\". "
            "The query can be anything; the cursor chooses the page."
        )
    elif cursor:
        parts.append("This is the last page.")
    return "\n".join(parts)
