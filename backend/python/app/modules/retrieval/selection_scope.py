"""A search selection below app level: what it covers and which records it admits.

A request may select record groups, folders or records beside whole apps. The
selection only narrows: what the user may read is still decided hit by hit by
``IGraphDBProvider.check_access``, which takes a scope from here to cite only a
record that lies inside the selection.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import (
    AGENT_BOUND_FILTER_KEYS,
    ALLOWED_FILTER_KEYS,
    CONNECTION_BOUND_FILTER_KEYS,
    CONTAINER_SCOPE_FILTER_KEYS,
    PROJECT_BOUND_FILTER_KEYS,
    SELECTION_APPS_FILTER_KEY,
    SELECTION_FILTER_KEYS,
    STRICT_SCOPE_FILTER_KEY,
    IGraphDBProvider,
)
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Written for the reader: the Node gateway passes a 404 or 422 message through.
SELECTION_TOO_LARGE_MESSAGE = (
    "The folders and items you selected hold too many records to search at once. "
    "Select the whole drive, space or app instead, or fewer items."
)
SELECTION_NOT_READY_MESSAGE = (
    "One of the sources you selected is still being prepared for folder-level search. "
    "Select the whole app for now, or try again later."
)
# The same two, when the user selected nothing and the limit is a saved agent's.
AGENT_LIMIT_TOO_LARGE_MESSAGE = (
    "This agent is limited to folders and items that hold too many records to search "
    "at once. Ask its owner to limit it to fewer items, or to use the whole source."
)
AGENT_LIMIT_NOT_READY_MESSAGE = (
    "One of the sources this agent is limited to is still being prepared for "
    "folder-level search. Try again later."
)
SELECTION_EMPTY_MESSAGE = (
    "Nothing in your selection is available to search. The items may have been "
    "removed, or you may no longer have access to them."
)
# Written for the reader, like the messages above: Node passes it through.
CONNECTION_OUTSIDE_MESSAGE = (
    "This item is not shared with this connected app. To change what the app can "
    "see, connect it again or create a new token."
)
CONNECTION_LIMIT_TOO_LARGE_MESSAGE = (
    "This connected app is limited to folders that hold too many records to check "
    "at once. Connect it again with fewer folders, or with whole sources."
)

#: Filter keys only the server may set. A client-sent copy is dropped.
SERVER_SET_FILTER_KEYS = frozenset((*ALLOWED_FILTER_KEYS, SELECTION_APPS_FILTER_KEY))

_MEMO_SECONDS = 30.0
_MEMO_MAX_ENTRIES = 256
_memo: dict[tuple, tuple[float, SelectionScope]] = {}


def max_selection_nodes() -> int:
    """Nested groups plus records one selection may cover. Read per call so a
    test run can lower it without a restart of every worker."""
    try:
        return max(1, int(os.getenv("SEARCH_SELECTION_MAX_NODES", "50000")))
    except (TypeError, ValueError):
        return 50_000


class SelectionTooLargeError(Exception):
    """The selection covers more nodes than ``max_selection_nodes``."""


class SelectionNotReadyError(Exception):
    """A selected source has no vector membership arrays to filter by yet."""


def selection_error(
    exc: SelectionTooLargeError | SelectionNotReadyError, *, agent_limit: bool = False,
) -> tuple[str, str]:
    """``(code, message)`` to answer a chat turn whose selection cannot be
    searched. ``agent_limit`` when the user selected nothing below app level
    and the selection is a saved agent's own limit."""
    if isinstance(exc, SelectionTooLargeError):
        return "selection_too_large", (
            AGENT_LIMIT_TOO_LARGE_MESSAGE if agent_limit else SELECTION_TOO_LARGE_MESSAGE
        )
    return "selection_not_ready", (
        AGENT_LIMIT_NOT_READY_MESSAGE if agent_limit else SELECTION_NOT_READY_MESSAGE
    )


@dataclass(frozen=True)
class SelectionScope:
    """What a selection covers, read from the hierarchy alone (no permissions)."""

    #: Apps and collections selected as a whole.
    app_ids: frozenset[str] = frozenset()
    #: Selected record groups and the groups nested under them, with their app.
    groups: Mapping[str, str] = field(default_factory=dict)
    #: Records under selected folders and records, as ``id -> (vrid, app id)``.
    #: A folder has no content: its vrid is empty.
    records: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def connector_ids(self) -> frozenset[str]:
        """Every app the selection touches."""
        return (
            self.app_ids
            | frozenset(self.groups.values())
            | frozenset(app for _, app in self.records.values())
        )

    @property
    def vrids(self) -> frozenset[str]:
        return frozenset(vrid for vrid, _ in self.records.values() if vrid)

    @property
    def is_empty(self) -> bool:
        """Whether the selection holds nothing a search could match."""
        return not (self.app_ids or self.groups or self.vrids)

    def admits(self, row: Mapping[str, Any]) -> bool:
        """Whether a ``check_access`` row is a node inside the selection: a
        selected app, group or record itself, or anything under one."""
        node_id = row.get("id")
        if node_id in self.records or node_id in self.groups or node_id in self.app_ids:
            return True
        if row.get("connectorId") in self.app_ids:
            return True
        return any(group_id in self.groups for group_id in row.get("groupIds") or ())

    def should_clauses(self, accessible_app_ids: Iterable[str] | None = None) -> dict[str, list[str]]:
        """The vector-filter alternatives that narrow a search to the selection.

        ``accessible_app_ids`` keeps only the whole apps the caller already
        knows the user reaches; groups and records are verified per hit.
        """
        apps = self.app_ids if accessible_app_ids is None else self.app_ids & frozenset(accessible_app_ids)
        should: dict[str, list[str]] = {}
        if apps:
            should[CONNECTOR_IDS_FIELD] = sorted(apps)
        if self.groups:
            should[RECORD_GROUP_IDS_FIELD] = sorted(self.groups)
        if self.vrids:
            should["virtualRecordId"] = sorted(self.vrids)
        return should


def _ids(filters: Mapping[str, Any] | None, key: str) -> list[str]:
    values = (filters or {}).get(key)
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v))


def has_selection(filters: Mapping[str, Any] | None) -> bool:
    """Whether the filters select anything below app level."""
    return any(_ids(filters, key) for key in SELECTION_FILTER_KEYS)


def allowed_filters(filters: Mapping[str, Any] | None) -> list[dict[str, list[str]]]:
    """Each server-set bound in ``filters`` (a saved agent's sources, a
    project's, a connected app's), as selection filters. A bound that is
    present but lists nothing admits nothing."""
    return [
        {target: _ids(filters, key) for key, target in keys.items()}
        for keys in (AGENT_BOUND_FILTER_KEYS, PROJECT_BOUND_FILTER_KEYS, CONNECTION_BOUND_FILTER_KEYS)
        if any(key in (filters or {}) for key in keys)
    ]


async def resolve_selection_scope(
    graph_provider: IGraphDBProvider,
    user_key: str,
    org_id: str,
    filters: Mapping[str, Any] | None,
    *,
    readable_only: bool = True,
) -> SelectionScope | None:
    """The scope ``filters`` selects, or None when they select whole apps only.

    A selected node the user cannot read is left out when ``readable_only``
    (a per-turn selection); an allow-list is taken as stored, since every hit
    is checked against the user anyway.

    Raises:
        SelectionTooLargeError: more nodes than ``max_selection_nodes``.
        PermissionVerificationUnavailableError: the graph could not answer.
    """
    group_ids, record_ids, exact_ids = (_ids(filters, key) for key in SELECTION_FILTER_KEYS)
    if not (group_ids or record_ids or exact_ids):
        return None
    app_ids = frozenset(i for key in CONTAINER_SCOPE_FILTER_KEYS for i in _ids(filters, key))

    memo_key = (org_id, user_key, readable_only, app_ids,
                frozenset(group_ids), frozenset(record_ids), frozenset(exact_ids))
    cached = _memo.get(memo_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    if readable_only:
        readable = (await graph_provider.check_access(
            user_key, org_id, node_ids=[*group_ids, *record_ids, *exact_ids],
        )).node_ids
        group_ids = [i for i in group_ids if i in readable]
        record_ids = [i for i in record_ids if i in readable]
        exact_ids = [i for i in exact_ids if i in readable]

    limit = max_selection_nodes()
    nodes: dict[str, list[dict[str, Any]]] = {"groups": [], "records": []}
    if group_ids or record_ids or exact_ids:
        nodes = await graph_provider.get_selection_nodes(
            org_id, group_ids=group_ids, record_ids=record_ids,
            exact_record_ids=exact_ids, limit=limit,
        )
    if nodes.get("truncated") or len(nodes["groups"]) + len(nodes["records"]) > limit:
        raise SelectionTooLargeError

    scope = SelectionScope(
        app_ids=app_ids,
        groups={g["id"]: g.get("connectorId") or "" for g in nodes["groups"] if g.get("id")},
        records={
            r["id"]: (r.get("vrid") or "", r.get("connectorId") or "")
            for r in nodes["records"]
            if r.get("id")
        },
    )
    if len(_memo) >= _MEMO_MAX_ENTRIES:
        _memo.clear()
    _memo[memo_key] = (time.monotonic() + _MEMO_SECONDS, scope)
    return scope


async def resolve_request_scopes(
    graph_provider: IGraphDBProvider,
    user_key: str,
    org_id: str,
    filters: Mapping[str, Any] | None,
) -> tuple[SelectionScope | None, list[SelectionScope]]:
    """The request's selection below app level (None without one) and every
    scope a cited record must lie in: the selection, a saved agent's sources
    and a project's sources."""
    selection = await resolve_selection_scope(graph_provider, user_key, org_id, filters)
    bounds = [
        await resolve_selection_scope(graph_provider, user_key, org_id, bound, readable_only=False)
        or SelectionScope(app_ids=frozenset(bound["apps"]))
        for bound in allowed_filters(filters)
    ]
    return selection, [*([selection] if selection is not None else []), *bounds]


def selection_app_ids(filters: Mapping[str, Any] | None) -> list[str] | None:
    """Every app a selection below app level touches, as recorded at the start
    of the turn, or None when the filters select whole apps only."""
    if not has_selection(filters):
        return None
    keys = (*CONTAINER_SCOPE_FILTER_KEYS, SELECTION_APPS_FILTER_KEY)
    return list(dict.fromkeys(i for key in keys for i in _ids(filters, key)))


async def require_membership_ready(
    graph_provider: IGraphDBProvider, scope: SelectionScope,
) -> None:
    """Raise ``SelectionNotReadyError`` when an app the selection filters by
    has no vector membership arrays: its points match no app or group term."""
    filtered_by_membership = scope.app_ids | frozenset(scope.groups.values())
    if not filtered_by_membership:
        return
    apps = await graph_provider.get_nodes_by_field_in(
        CollectionNames.APPS.value, "id", sorted(filtered_by_membership),
        return_fields=["id", "vectorMembershipBackfilled", "vectorMembershipBackfillExhausted"],
        raise_on_error=True,
    )
    # Ids with no App document (a sentinel, an attachment id) are simply absent.
    for app in apps or []:
        if not app.get("vectorMembershipBackfilled") or app.get("vectorMembershipBackfillExhausted"):
            raise SelectionNotReadyError


def _set_bound(
    turn: dict[str, Any], keys: Mapping[str, str], sources: Mapping[str, Iterable[str]] | None,
) -> None:
    if sources is not None:
        turn.update({key: sorted(set(sources.get(target) or ())) for key, target in keys.items()})


def within_connection(
    filters: Mapping[str, Any] | None, connection_sources: Mapping[str, Iterable[str]] | None,
) -> dict[str, Any]:
    """``filters`` as a client sent them, with every server-set key dropped and
    a connected app's limit set as a bound. A request that picks nothing picks
    the whole limit, so the vector search runs inside it rather than every hit
    outside it being dropped after. The turn is always strict: a tool left with
    no app to work on must reach nothing, never every app the user can read."""
    turn = {key: value for key, value in (filters or {}).items() if key not in SERVER_SET_FILTER_KEYS}
    if connection_sources is None:
        return turn
    _set_bound(turn, CONNECTION_BOUND_FILTER_KEYS, connection_sources)
    picked = any(_ids(turn, key) for key in (*CONTAINER_SCOPE_FILTER_KEYS, *SELECTION_FILTER_KEYS))
    if not picked:
        turn.update({target: turn[key] for key, target in CONNECTION_BOUND_FILTER_KEYS.items()})
    turn[STRICT_SCOPE_FILTER_KEY] = True
    return turn


async def outside_connection(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    node_ids: Iterable[str],
    connection_sources: Mapping[str, Iterable[str]] | None,
) -> set[str]:
    """Of ``node_ids``, those a connected app limited to ``connection_sources``
    may not reach by id; none when the app has no limit.

    Raises:
        SelectionTooLargeError, PermissionVerificationUnavailableError: see
            ``resolve_selection_scope``.
    """
    ids = [i for i in dict.fromkeys(node_ids) if i]
    if connection_sources is None or not ids:
        return set()
    user = await graph_provider.get_user_by_user_id(user_id=user_id)
    user_key = (user.get("_key") or user.get("id")) if user else None
    if not user_key:
        return set(ids)
    bound: dict[str, Any] = {}
    _set_bound(bound, CONNECTION_BOUND_FILTER_KEYS, connection_sources)
    _, scopes = await resolve_request_scopes(graph_provider, user_key, org_id, bound)
    access = await graph_provider.check_access(user_key, org_id, node_ids=ids, scopes=scopes)
    return set(ids) - set(access.node_ids_in_scope)


async def connection_coverage(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    connection_sources: Mapping[str, Iterable[str]],
) -> tuple[frozenset[str], frozenset[str]]:
    """The apps and collections a connected app's limit names whole, and those
    it reaches only in part, through the folders and files it names in them.
    Both empty when the user is unknown.

    Raises:
        SelectionTooLargeError, PermissionVerificationUnavailableError: see
            ``resolve_selection_scope``.
    """
    user = await graph_provider.get_user_by_user_id(user_id=user_id)
    user_key = (user.get("_key") or user.get("id")) if user else None
    if not user_key:
        return frozenset(), frozenset()
    bound: dict[str, Any] = {}
    _set_bound(bound, CONNECTION_BOUND_FILTER_KEYS, connection_sources)
    _, (scope,) = await resolve_request_scopes(graph_provider, user_key, org_id, bound)
    return scope.app_ids, scope.connector_ids - scope.app_ids


async def prepare_turn_scope(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    filters: Mapping[str, Any] | None,
    *,
    agent_sources: Mapping[str, Iterable[str]] | None = None,
    project_sources: Mapping[str, Iterable[str]] | None = None,
    connection_sources: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """A chat turn's filters with the server-set keys in place.

    Client-sent copies of the server-set keys are always dropped, and
    ``agent_sources`` (``apps`` / ``recordGroups`` / ``records``, as stored on
    a saved agent) always bound what the turn may cite or read by id. Without
    a selection below app level nothing else changes. With one, the selection
    is resolved once so that a selection that cannot be searched fails the
    turn before the model runs, the apps it touches are recorded for the tools
    that work per app, and ``project_sources`` (same shape, a project's) bound
    it too. ``connection_sources`` (same shape, what the user let the connected
    app making the request see) always bounds the turn; see ``within_connection``.

    Raises:
        SelectionTooLargeError, SelectionNotReadyError: see ``resolve_selection_scope``
            and ``require_membership_ready``.
        PermissionVerificationUnavailableError: the graph could not answer.
    """
    turn = within_connection(filters, connection_sources)

    def bind(keys: Mapping[str, str], sources: Mapping[str, Iterable[str]] | None) -> None:
        if sources is not None:
            turn.update({key: sorted(set(sources.get(target) or ())) for key, target in keys.items()})

    # Without a selection a whole-source agent still bounds the tools that
    # read a record by id, which no search filter narrows.
    bind(AGENT_BOUND_FILTER_KEYS, agent_sources)
    if not has_selection(turn):
        return turn
    # The selection is the whole scope of the turn: where it leaves a tool
    # with no app to work on, that means nothing, never everything.
    turn[STRICT_SCOPE_FILTER_KEY] = True
    bind(PROJECT_BOUND_FILTER_KEYS, project_sources)
    user = await graph_provider.get_user_by_user_id(user_id=user_id)
    user_key = (user.get("_key") or user.get("id")) if user else None
    scope = (await resolve_request_scopes(graph_provider, user_key, org_id, turn))[0] if user_key else None
    if scope is not None and not scope.is_empty:
        await require_membership_ready(graph_provider, scope)
        turn[SELECTION_APPS_FILTER_KEY] = sorted(scope.connector_ids - scope.app_ids)
    return turn


async def unreadable_scope_error(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    filters: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """``(code, message)`` when the user can read nothing the turn's filters
    admit, so a turn that can only search answers at once rather than
    searching in vain until its time runs out. None when something may be
    readable, or when that cannot be told cheaply: the search then says so.
    Distinct from a search that finds no hits.
    """
    if has_selection(filters):
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        user_key = (user.get("_key") or user.get("id")) if user else None
        if not user_key:
            return None
        # Memoized: prepare_turn_scope resolved the same selection a moment ago.
        selection = await resolve_selection_scope(graph_provider, user_key, org_id, filters)
        if selection is not None and selection.is_empty:
            return "selection_empty", SELECTION_EMPTY_MESSAGE
        return None
    from app.modules.retrieval.retrieval_service import (
        ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE,
    )

    # Only the containers: every other filter narrows further, so nothing in
    # the wider scope means nothing in the turn's.
    containers = await graph_provider.get_accessible_containers(
        user_id, org_id, {key: (filters or {})[key] for key in CONTAINER_SCOPE_FILTER_KEYS if key in (filters or {})},
    )
    if containers.fallback_reason is None and containers.is_empty:
        return "accessible_records_not_found", ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE
    return None
