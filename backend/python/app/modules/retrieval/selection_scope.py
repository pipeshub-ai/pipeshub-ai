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

#: Sent in ``kb`` when a saved agent's turn picks no collection; not an id.
NO_KB_SELECTED_FILTER = "NO_KB_SELECTED"

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

    def __init__(self, message: str = SELECTION_TOO_LARGE_MESSAGE) -> None:
        super().__init__(message)


class SelectionNotReadyError(Exception):
    """A selected source has no vector membership arrays to filter by yet."""

    def __init__(self, message: str = SELECTION_NOT_READY_MESSAGE) -> None:
        super().__init__(message)


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
    project's), as selection filters. A bound that is present but lists
    nothing admits nothing."""
    return [
        {target: _ids(filters, key) for key, target in keys.items()}
        for keys in (AGENT_BOUND_FILTER_KEYS, PROJECT_BOUND_FILTER_KEYS)
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
    if len(nodes["groups"]) + len(nodes["records"]) > limit:
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


def picked_app_ids(filters: Mapping[str, Any] | None, *, ignore: Iterable[str] = ()) -> frozenset[str]:
    """The apps and collections ``filters`` pick whole."""
    ignored = {NO_KB_SELECTED_FILTER, *ignore}
    return frozenset(i for key in CONTAINER_SCOPE_FILTER_KEYS for i in _ids(filters, key) if i not in ignored)


def limits_records(filters: Mapping[str, Any] | None, *, ignore: Iterable[str] = ()) -> bool:
    """Whether the turn reaches less than everything the user may read."""
    return bool(
        has_selection(filters) or allowed_filters(filters)
        or picked_app_ids(filters, ignore=ignore) or (filters or {}).get(STRICT_SCOPE_FILTER_KEY)
    )


async def resolve_record_scopes(
    graph_provider: IGraphDBProvider,
    user_key: str,
    org_id: str,
    filters: Mapping[str, Any] | None,
    *,
    ignore: Iterable[str] = (),
) -> list[SelectionScope] | None:
    """Every scope a record reached by id or by a walk of the graph, rather
    than by a search, must lie in; None when the turn is not limited.

    A search keeps to the apps picked whole through its vector filter; these
    paths have no such filter, so those apps are a scope here as well. No app
    picked means every source the user may read, unless the turn is strict.
    ``ignore`` names ids in ``kb`` that are not sources (an attachment's).

    Raises:
        SelectionTooLargeError, PermissionVerificationUnavailableError: see
            ``resolve_selection_scope``.
    """
    if not limits_records(filters, ignore=ignore):
        return None
    selection, scopes = await resolve_request_scopes(graph_provider, user_key, org_id, filters)
    picked = picked_app_ids(filters, ignore=ignore)
    if selection is None and (picked or (filters or {}).get(STRICT_SCOPE_FILTER_KEY)):
        scopes = [SelectionScope(app_ids=picked), *scopes]
    return scopes


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


async def prepare_turn_scope(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    filters: Mapping[str, Any] | None,
    *,
    agent_sources: Mapping[str, Iterable[str]] | None = None,
    project_sources: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """A chat turn's filters with the server-set keys in place.

    Client-sent copies of the server-set keys are always dropped.
    ``agent_sources`` and ``project_sources`` (each ``apps`` /
    ``recordGroups`` / ``records``, as stored on a saved agent or a project)
    always bound what a search may cite and a tool may reach by id; a
    service-account agent runs as its creator, so its knowledge is the only
    limit there. A selection below app level, and a bound below app level, is
    resolved once here so that one that cannot be searched fails the turn
    before the model runs, with its reason, rather than inside a tool. The
    apps a selection touches are recorded for the tools that work per app.

    Raises:
        SelectionTooLargeError, SelectionNotReadyError: see ``resolve_selection_scope``
            and ``require_membership_ready``.
        PermissionVerificationUnavailableError: the graph could not answer.
    """
    turn = {key: value for key, value in (filters or {}).items() if key not in SERVER_SET_FILTER_KEYS}
    _set_bound(turn, AGENT_BOUND_FILTER_KEYS, agent_sources)
    _set_bound(turn, PROJECT_BOUND_FILTER_KEYS, project_sources)
    selected = has_selection(turn)
    if not selected and not any(has_selection(bound) for bound in allowed_filters(turn)):
        return turn
    if selected:
        # The selection is the whole scope of the turn: where it leaves a tool
        # with no app to work on, that means nothing, never everything.
        turn[STRICT_SCOPE_FILTER_KEY] = True
    user = await graph_provider.get_user_by_user_id(user_id=user_id)
    user_key = (user.get("_key") or user.get("id")) if user else None
    scope = (await resolve_request_scopes(graph_provider, user_key, org_id, turn))[0] if user_key else None
    if scope is not None and not scope.is_empty:
        await require_membership_ready(graph_provider, scope)
        turn[SELECTION_APPS_FILTER_KEY] = sorted(scope.connector_ids - scope.app_ids)
    return turn
