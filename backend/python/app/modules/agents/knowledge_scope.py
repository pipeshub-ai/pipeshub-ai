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

from app.config.constants.arangodb import AccessLimit, CollectionNames, Connectors
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.modules.agents.capability_summary import PROJECT_FILES_KEY
from app.modules.agents.qna.chat_state import (
    _extract_kb_app_ids,
    _extract_knowledge_connector_ids,
)
from app.modules.retrieval.selection_scope import SERVER_SET_FILTER_KEYS, has_selection
from app.utils.connector_instances import fetch_user_connector_instances
from app.utils.user_lookup import get_caller_user

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

NO_KB_SELECTED_FILTER = "NO_KB_SELECTED"

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


def source_reach(entries: Iterable[object]) -> dict[str, tuple[tuple[str, ...], ...] | None]:
    """What each knowledge source lets an agent search, read the way search reads
    it: ``None`` for a whole source, else its sorted record groups and records.
    Two spellings of the same reach compare equal, so a save that resends an
    agent's sources unchanged is not mistaken for a change.
    """
    items = [e for e in entries if isinstance(e, dict) and str(e.get("connectorId") or "").strip()]
    source_ids = {str(e["connectorId"]).strip() for e in items}
    reach: dict[str, tuple[tuple[str, ...], ...] | None] = {}
    for entry in items:
        source = str(entry["connectorId"]).strip()
        limit = _narrowed_to(entry, source_ids)
        # As in resolve_agent_filters: a source listed twice is searched with its
        # last limit, and a whole-source entry does not lift an earlier limit.
        if limit is None:
            reach.setdefault(source, None)
        else:
            reach[source] = tuple(tuple(sorted(limit[key])) for key in _NARROWING_KEYS)
    return reach


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
        caller = await get_caller_user(graph_provider, caller_user_id)
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


PROJECT_FILES_LABEL = "Project files"


async def project_files_knowledge(
    graph_provider: IGraphDBProvider,
    *,
    kb_ids: Iterable[str],
    known_ids: Iterable[str],
    logger: Logger,
) -> list[dict[str, Any]]:
    """A knowledge entry for each hidden collection in the turn's ``kb``
    filter that ``known_ids`` does not already list: a project's own file
    collection, which Node adds to ``kb`` for every project chat.

    Hidden collections are left out of the user's listed collections, so
    without this the agent neither lists nor searches the files uploaded to
    the project (search scopes to the knowledge list). A lookup failure adds
    nothing.
    """
    known = set(known_ids)
    candidates = [
        k for k in dict.fromkeys(kb_ids)
        if k and k != NO_KB_SELECTED_FILTER and k not in known
    ][:MAX_COLLECTION_CANDIDATES]
    if not candidates:
        return []
    try:
        hidden = set(await graph_provider.get_hidden_app_ids(candidates))
    except Exception:
        logger.warning("Could not look up project file collections %s", candidates, exc_info=True)
        return []
    return [
        {
            "connectorId": kb_id,
            "name": PROJECT_FILES_LABEL,
            "displayName": PROJECT_FILES_LABEL,
            "type": Connectors.KNOWLEDGE_BASE.value,
            "filters": {},
            "filtersParsed": {"records": []},
            PROJECT_FILES_KEY: True,
        }
        for kb_id in candidates if kb_id in hidden
    ]


def _is_collection(entry: dict[str, Any]) -> bool:
    return str(entry.get("type") or "").strip().upper() == Connectors.KNOWLEDGE_BASE.value


async def service_account_knowledge(
    graph_provider: IGraphDBProvider,
    agent_knowledge: Sequence[object],
    user_id: str,
    org_id: str,
    logger: Logger,
    *,
    bound: bool,
) -> list[dict[str, Any]]:
    """The part of a service-account agent's knowledge it may search.

    Anyone in the org runs such an agent on one identity, so it keeps only what
    is shared with the whole org: the org's team connectors, never a personal
    one, nor one whose access limit admits less than the whole org (the agent's
    creator passes a limit to the creator, its callers must not). Collections have no org-wide grant, so one is kept only when the agent
    runs as a bound service account, whose own grants then decide what of it is
    read. A failed connector lookup keeps no connector, and a source whose app
    could not be read is kept only if the lookup lists it as a team connector.
    """
    knowledge = [k for k in agent_knowledge or [] if isinstance(k, dict)]
    team: set[str] = set()
    if any(not _is_collection(k) for k in knowledge):
        instances = await fetch_user_connector_instances(graph_provider, user_id, org_id, logger)
        # Typed from the lookup, not the entry: an entry the graph could not resolve is typed UNKNOWN.
        team = {
            str(i.get("_key") or i.get("id"))
            for i in instances
            if isinstance(i, dict) and i.get("scope") == ConnectorScope.TEAM.value and not _is_collection(i)
            and i.get("accessLimit") in (None, AccessLimit.ORG.value)
        }
    kept = [
        k for k in knowledge
        if (bound if _is_collection(k) else str(k.get("connectorId") or "").strip() in team)
    ]
    if len(kept) < len(knowledge):
        logger.info(
            "Service-account agent: %d knowledge source(s) not shared with the org are not searched",
            len(knowledge) - len(kept),
        )
    return kept


async def hide_names_the_viewer_cannot_open(
    graph_provider: IGraphDBProvider,
    knowledge_lists: Iterable[Sequence[object]],
    user_id: str,
    org_id: str,
    *,
    is_admin: bool,
) -> set[str]:
    """Show, in place, the id instead of the name of every knowledge source whose connector is limited (``accessLimit``, which
    ``get_agent`` and ``get_all_agents`` carry on each source) and does not admit the viewer, as a source that cannot be resolved
    reads. The limit is weighed by the connector list a member gets (``apply_access_limit``), asked only when a source is limited.
    A failed lookup admits nothing, so it hides the name. The node titles saved in ``filters`` and ``filtersParsed`` are left out
    with it. An admin of the request's org sees every name, as on the connectors page. Returns the ids of the connectors hidden,
    so a caller leaves out what else would name them (a chat turn's connector configs)."""
    refused = await _refused_sources(graph_provider, knowledge_lists, user_id, org_id, is_admin=is_admin)
    for k in refused:
        k["name"] = k["displayName"] = k.get("connectorId")
        # The folder and file titles the agent builder saved go too; the ids that limit the search stay.
        for key in ("filters", "filtersParsed"):
            if key in k:
                k[key] = _without_titles(k[key])
    return {str(k.get("connectorId")) for k in refused}


async def with_current_limits(graph_provider: IGraphDBProvider, knowledge: Sequence[object]) -> None:
    """Set, in place, each source's ``accessLimit`` to its connector's as it is now. A published version (and so an export
    of one) froze the limit its sources had then; a connector limited since must be weighed as it is. A connector that
    is gone is weighed as limited to its creator. A failed read raises, rather than reading as no limit."""
    for k in knowledge or []:
        if isinstance(k, dict) and k.get("connectorId"):
            app = await graph_provider.get_document(
                str(k["connectorId"]), CollectionNames.APPS.value, raise_on_error=True,
            )
            # A connector that is gone is weighed as limited, so its name becomes its id, as live get_agent names it.
            k["accessLimit"] = app.get("accessLimit") if app else AccessLimit.CREATOR.value


async def hide_limited_names(
    graph_provider: IGraphDBProvider,
    knowledge: list[dict[str, Any]],
    user_id: str,
    org_id: str,
    logger: Logger,
    *,
    is_admin: bool,
) -> set[str]:
    """For a turn's knowledge: each source's limit as it is now (``with_current_limits``; a published version froze
    an older one, or none), then ``hide_names_the_viewer_cannot_open``. A limit that cannot be read is weighed as
    limited to the creator, so the viewer's own connector list decides. Returns the hidden connector ids."""
    try:
        await with_current_limits(graph_provider, knowledge)
    except Exception:
        logger.warning("Connector limits could not be read; every source is weighed as limited", exc_info=True)
        for k in knowledge:
            if isinstance(k, dict) and k.get("connectorId"):
                k["accessLimit"] = AccessLimit.CREATOR.value
    return await hide_names_the_viewer_cannot_open(graph_provider, [knowledge], user_id, org_id, is_admin=is_admin)


async def _refused_sources(
    graph_provider: IGraphDBProvider, knowledge_lists: Iterable[Sequence[object]], user_id: str, org_id: str, *, is_admin: bool,
) -> list[dict[str, Any]]:
    """The knowledge sources whose connector is limited and does not admit the user; none for an admin. A failed lookup admits
    nothing."""
    if is_admin:
        return []
    limited = [k for ks in knowledge_lists for k in ks or [] if isinstance(k, dict) and k.get("accessLimit") is not None]
    if not limited:
        return []
    try:
        listed = await graph_provider.get_user_connector_instances(
            collection=CollectionNames.APPS.value, user_id=user_id, org_id=org_id, team_scope=ConnectorScope.TEAM.value,
            personal_scope=ConnectorScope.PERSONAL.value, apply_access_limit=True,
        ) or []
    except Exception:  # a failed lookup admits nothing
        listed = []
    opens = {str(d.get("_key") or d.get("id")) for d in listed if isinstance(d, dict)}
    return [k for k in limited if str(k.get("connectorId") or "") not in opens]


async def keep_hidden_titles(
    graph_provider: IGraphDBProvider,
    incoming: dict[str, dict[str, Any]],
    stored_knowledge: Sequence[object],
    user_id: str,
    org_id: str,
    *,
    is_admin: bool,
) -> None:
    """On an agent save, keep in ``incoming`` (``_parse_knowledge_sources``' output) the saved node titles of every source the
    limit refuses the editor: the editor was shown that source with its titles left out (``hide_names_the_viewer_cannot_open``),
    so their copy must not erase them. Their ids and choices stand; a node the source had saved keeps its stored title. When the
    stored ``filters`` cannot be read, the stored value is kept as it is. An admin, or an editor the limit admits, saves as sent."""
    stored = {str(k.get("connectorId")): k for k in stored_knowledge or [] if isinstance(k, dict) and k.get("connectorId")}
    touched = [stored[cid] for cid in incoming if cid in stored]
    for k in await _refused_sources(graph_provider, [touched], user_id, org_id, is_admin=is_admin):
        cid = str(k["connectorId"])
        raw = k.get("filters")
        saved: object = raw if isinstance(raw, dict) else None
        if isinstance(raw, str):
            try:
                saved = json.loads(raw) if raw.strip() else {}
            except ValueError:
                saved = None
        elif raw is None:
            saved = {}
        if not isinstance(saved, dict):
            incoming[cid]["filters"] = raw  # unreadable: what is stored stays
            continue
        titles = {n["id"]: n for n in saved.get("nodes") or [] if isinstance(n, dict) and isinstance(n.get("id"), str) and n.get("name")}
        if not titles:
            continue
        filters = incoming[cid]["filters"]
        nodes = [dict(n, name=titles[n["id"]]["name"]) if isinstance(n, dict) and n.get("id") in titles else n
                 for n in filters.get("nodes") or []]
        present = {n.get("id") for n in nodes if isinstance(n, dict)}
        chosen = [i for key in ("recordGroups", "records") for i in filters.get(key) or [] if isinstance(i, str)]
        nodes += [dict(titles[i]) for i in dict.fromkeys(chosen) if i in titles and i not in present]
        incoming[cid]["filters"] = {**filters, "nodes": nodes}


def _without_titles(filters: object) -> object:
    """``filters`` with the ``name`` of each saved node (``nodes[].name``) left out, as a JSON string when it came as one. A
    value that is not such an object is returned as it is: an unreadable limit must stay unreadable, never become no limit."""
    parsed = filters
    if isinstance(filters, str):
        try:
            parsed = json.loads(filters)
        except ValueError:
            return filters
    if not isinstance(parsed, dict) or not isinstance(parsed.get("nodes"), list):
        return filters
    stripped = {**parsed, "nodes": [{f: v for f, v in n.items() if f != "name"} if isinstance(n, dict) else n for n in parsed["nodes"]]}
    return json.dumps(stripped) if isinstance(filters, str) else stripped


__all__ = [
    "MAX_CALLER_COLLECTIONS",
    "MAX_COLLECTION_CANDIDATES",
    "NO_KB_SELECTED_FILTER",
    "PROJECT_FILES_LABEL",
    "AgentScope",
    "UnreadableSourceLimitError",
    "admit_caller_project_collections",
    "hide_names_the_viewer_cannot_open",
    "keep_hidden_titles",
    "project_files_knowledge",
    "read_source_limit",
    "resolve_agent_filters",
    "service_account_knowledge",
]
