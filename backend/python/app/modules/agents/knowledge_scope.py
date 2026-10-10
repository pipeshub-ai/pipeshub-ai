"""The sources one agent turn may search.

A saved agent's configured knowledge is the ceiling for every turn: the chat's
source picker and a project scope may narrow it, never add to it. This matters
beyond product scoping because a service-account agent runs retrieval as its
creator, so a source id the caller adds would be searched with the creator's
permissions.

The one kind of id a saved agent accepts from outside its knowledge is a hidden
collection, which is what Node adds to ``kb`` for a project chat (the project's
linked files). It is admitted only when the caller, never the run-as identity,
holds a role on it. Python cannot see which project a collection belongs to, so
the bound is "a hidden collection the caller can read", not "this chat's
project". Retrieval still runs as the run-as identity, so for a service-account
agent an admitted collection returns only what the creator can also read.

A source may be limited to some of its record groups, folders or records
(``filters.recordGroups`` / ``filters.records`` on the knowledge entry). Such
a source is never searched as a whole: a turn that enables it searches those
nodes, and a selection the caller makes must lie under them.

The universal agent (``agentIdPlaceholder``) has no curated knowledge; its
filters are the caller's own selection, bounded by the caller's permissions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors
from app.modules.agents.qna.chat_state import (
    _extract_kb_app_ids,
    _extract_knowledge_connector_ids,
)
from app.modules.retrieval.selection_scope import (
    NO_KB_SELECTED_FILTER,
    SERVER_SET_FILTER_KEYS,
    has_selection,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Ids looked up in one batched query. A project scope sends its collections
# with the hidden one last, so the bound must comfortably exceed a project's
# collection count or the one that matters is never looked at.
MAX_COLLECTION_CANDIDATES = 50
# Hidden collections permission-checked per turn; a project chat adds one.
MAX_CALLER_COLLECTIONS = 5


@dataclass(frozen=True)
class AgentScope:
    filters: dict[str, Any]
    dropped_app_ids: tuple[str, ...] = ()
    dropped_kb_ids: tuple[str, ...] = ()
    #: Everything a saved agent may search, as ``apps`` (whole sources),
    #: ``recordGroups`` and ``records``. Empty for the universal agent, which
    #: has no bound of its own.
    sources: dict[str, list[str]] = field(default_factory=dict)
    #: Sources whose stored limit could not be read; they are not searched.
    unreadable_source_ids: tuple[str, ...] = ()


_NARROWING_KEYS = ("recordGroups", "records")


class UnreadableSourceLimitError(ValueError):
    """A knowledge source's stored ``filters`` are not a limit that can be applied."""


def read_source_limit(stored: object) -> dict[str, list[str]]:
    """The record groups and records a knowledge source's ``filters`` list;
    empty lists when they list none.

    Raises:
        UnreadableSourceLimitError: the value is not an object (or the JSON
            text of one), or a list in it is not a list of ids. Such a limit
            must be refused when saved and must limit the source to nothing
            when read: it never means the whole source.
    """
    if isinstance(stored, str):
        if not stored.strip():
            return {key: [] for key in _NARROWING_KEYS}
        try:
            stored = json.loads(stored)
        except ValueError as exc:
            raise UnreadableSourceLimitError from exc
    if stored is None or stored == []:
        return {key: [] for key in _NARROWING_KEYS}
    if not isinstance(stored, dict):
        raise UnreadableSourceLimitError
    limit: dict[str, list[str]] = {}
    for key in _NARROWING_KEYS:
        ids = stored.get(key)
        if ids is None:
            limit[key] = []
        elif isinstance(ids, list) and all(isinstance(i, str) and i.strip() for i in ids):
            limit[key] = list(dict.fromkeys(i.strip() for i in ids))
        else:
            raise UnreadableSourceLimitError
    return limit


def _narrowed_to(entry: dict[str, Any], source_ids: set[str]) -> dict[str, list[str]] | None:
    """The record groups and records a knowledge source is limited to, or None
    for a whole source. A stored limit that cannot be read limits the source
    to nothing. An id that is itself a source is not a limit: agents saved
    before each collection became its own source listed the collection there.
    """
    # The stored text is read here: the graph's parsed copy turns a value it
    # cannot parse into "no filters".
    stored = entry.get("filters")
    if not (isinstance(stored, str) and stored.strip()):
        stored = entry.get("filtersParsed", stored)
    try:
        limit = read_source_limit(stored)
    except UnreadableSourceLimitError:
        return {key: [] for key in _NARROWING_KEYS}
    limit = {key: [i for i in ids if i not in source_ids] for key, ids in limit.items()}
    return limit if any(limit.values()) else None


def _clean_ids(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def _requested_ids(value: object) -> list[str] | None:
    """``None`` when the bucket was not given; otherwise its distinct ids."""
    if value is None:
        return None
    if not isinstance(value, list):
        return []
    return _clean_ids(value)


def _within(
    requested: list[str] | None, ceiling: Sequence[str]
) -> tuple[list[str], tuple[str, ...]]:
    if requested is None:
        return list(ceiling), ()
    allowed = set(ceiling)
    kept = [i for i in requested if i in allowed]
    dropped = tuple(i for i in requested if i not in allowed and i != NO_KB_SELECTED_FILTER)
    return kept, dropped


def resolve_agent_filters(
    agent_knowledge: Sequence[object],
    requested_filters: dict[str, Any] | None,
    *,
    is_universal_agent: bool,
) -> AgentScope:
    """The turn's ``filters``: the requested ``apps``/``kb`` intersected with
    the agent's own sources of the same type. An absent or ``None`` bucket
    means all of the agent's sources of that type; an empty list means none.
    Other keys pass through, since they only narrow."""
    knowledge = [k for k in agent_knowledge or [] if isinstance(k, dict)]
    agent_apps = _clean_ids(_extract_knowledge_connector_ids(knowledge))
    agent_kbs = _clean_ids(_extract_kb_app_ids(knowledge))
    filters = {k: v for k, v in (requested_filters or {}).items() if k not in SERVER_SET_FILTER_KEYS}

    # A collection may arrive under ``apps`` like any other app; the buckets
    # below are typed, so it is moved to the one it is checked against.
    requested_apps = filters.get("apps")
    if isinstance(requested_apps, list):
        collections = [i for i in requested_apps if i in set(agent_kbs)]
        if collections:
            filters["apps"] = [i for i in requested_apps if i not in collections]
            filters["kb"] = list(dict.fromkeys([*(filters.get("kb") or []), *collections]))

    # A selection below app level is the whole scope of the turn: a bucket
    # that was not given means none of that kind, never all of them.
    if has_selection(filters):
        for bucket in ("apps", "kb"):
            if filters.get(bucket) is None:
                filters[bucket] = []

    if is_universal_agent:
        if filters.get("apps") is None:
            filters["apps"] = agent_apps
        if filters.get("kb") is None:
            filters["kb"] = agent_kbs
        return AgentScope(filters=filters)

    apps, dropped_apps = _within(_requested_ids(filters.get("apps")), agent_apps)
    kbs, dropped_kbs = _within(_requested_ids(filters.get("kb")), agent_kbs)

    source_ids = {*agent_apps, *agent_kbs}
    narrowed = {
        str(k.get("connectorId")).strip(): lists
        for k in knowledge
        if (lists := _narrowed_to(k, source_ids)) is not None
    }
    # An enabled source that is limited to some nodes is searched as those
    # nodes, which makes the turn a selection below app level.
    for key in _NARROWING_KEYS:
        enabled = [i for source in (*apps, *kbs) for i in narrowed.get(source, {}).get(key, [])]
        if enabled:
            filters[key] = _clean_ids([*(filters.get(key) or []), *enabled])
    filters["apps"] = [i for i in apps if i not in narrowed]
    filters["kb"] = [i for i in kbs if i not in narrowed]
    sources = {
        "apps": [i for i in (*agent_apps, *agent_kbs) if i not in narrowed],
        **{key: _clean_ids(i for lists in narrowed.values() for i in lists[key]) for key in _NARROWING_KEYS},
    }
    return AgentScope(
        filters=filters, dropped_app_ids=dropped_apps, dropped_kb_ids=dropped_kbs, sources=sources,
        unreadable_source_ids=tuple(i for i, lists in narrowed.items() if not any(lists.values())),
    )


async def admit_caller_project_collections(
    graph_provider: IGraphDBProvider,
    *,
    kb_ids: Iterable[str],
    caller_user_id: str,
    org_id: str,
    logger: Logger,
) -> list[str]:
    """The ids in ``kb_ids`` that are hidden collections of ``org_id`` on which
    the caller holds a role, in request order. Any lookup failure admits
    nothing."""
    candidates = list(dict.fromkeys(kb_ids))[:MAX_COLLECTION_CANDIDATES]
    if not candidates or not caller_user_id:
        return []
    try:
        caller = await graph_provider.get_user_by_user_id(caller_user_id)
        caller_key = (caller or {}).get("_key") or (caller or {}).get("id")
        if not caller_key:
            return []
        rows = await graph_provider.get_nodes_by_field_in(
            CollectionNames.APPS.value, "id", candidates,
            return_fields=["id", "type", "isHidden", "orgId"],
        )
    except Exception:
        logger.warning(
            "Could not check collections %s for caller %s (org %s); none admitted",
            candidates, caller_user_id, org_id, exc_info=True,
        )
        return []

    hidden = {
        str(row.get("id") or row.get("_key"))
        for row in rows or []
        if row.get("type") == Connectors.KNOWLEDGE_BASE.value
        and row.get("isHidden") is True
        and row.get("orgId") == org_id
    }
    admitted: list[str] = []
    for kb_id in [k for k in candidates if k in hidden][:MAX_CALLER_COLLECTIONS]:
        try:
            if await graph_provider.get_user_kb_permission(kb_id, caller_key):
                admitted.append(kb_id)
        except Exception:
            logger.warning(
                "Permission check failed for collection %s (caller %s, org %s); not admitted",
                kb_id, caller_user_id, org_id, exc_info=True,
            )
    return admitted


__all__ = [
    "MAX_CALLER_COLLECTIONS",
    "MAX_COLLECTION_CANDIDATES",
    "NO_KB_SELECTED_FILTER",
    "AgentScope",
    "UnreadableSourceLimitError",
    "admit_caller_project_collections",
    "read_source_limit",
    "resolve_agent_filters",
]
