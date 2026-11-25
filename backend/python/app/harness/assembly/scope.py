"""A durable run's turn scope (SYNC4, main #4255): the pin read back as the request, resolved every attempt.

Node pins the ids the request selected below app level and a saved agent's per-source limits, never what they
expand to or whether the user may read them. Each attempt hands the pin to main's own turn-scope code
(``_resolve_turn_filters`` / ``prepare_turn_scope``), so a revocation applies at the next attempt and a selection
that became unreadable fails the run with main's code.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from harness_core.driver.refusal import RunRefused

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.harness.assembly.context import KnowledgeScope, SourceLimit
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_SELECTION = (("recordGroups", "record_groups"), ("records", "records"), ("recordsExact", "records_exact"))


def requested_filters(knowledge: KnowledgeScope) -> dict[str, Any]:
    """The pinned knowledge as the request's ``filters``: absent when nothing narrows (the built-in assistant with
    nothing picked), else ``apps``/``kb`` and the selection, as the client sent them after Node narrowed them."""
    selection = {key: list(getattr(knowledge, attr)) for key, attr in _SELECTION if getattr(knowledge, attr)}
    if not (knowledge.apps or knowledge.kb or selection or knowledge.strict_scope):
        return {}
    return {"apps": list(knowledge.apps), "kb": list(knowledge.kb), **selection}


def _live_limit(entry: Mapping[str, Any]) -> dict[str, list[str]] | None:
    """The live entry's limit as main reads it; None for a whole source. A limit that cannot be read limits the
    source to nothing (main's ``read_source_limit`` rule)."""
    from app.modules.agents.knowledge_scope import (
        UnreadableSourceLimitError,
        read_source_limit,
    )

    stored = entry.get("filters")
    if not (isinstance(stored, str) and stored.strip()):
        stored = entry.get("filtersParsed", stored)
    try:
        limit = read_source_limit(stored)
    except UnreadableSourceLimitError:
        return {"recordGroups": [], "records": []}
    return limit if any(limit.values()) else None


async def bounded_agent_knowledge(
    live: Sequence[object], pinned: Mapping[str, SourceLimit], graph: IGraphDBProvider, org_id: str,
) -> list[dict[str, Any]]:
    """The live agent's knowledge entries, each limited to no more than admission pinned (S12).

    A source limited at admission keeps at most what those ids cover however the owner changed it since; a source
    the owner narrowed since is narrowed now. Where both name ids, a node is kept when it lies inside the other side's
    limit (itself, or under one of its groups or folders, read from the hierarchy), and where none are left the
    source is not searched at all: never the whole source.
    """
    out: list[dict[str, Any]] = []
    for item in live:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = dict(item)
        pin = pinned.get(str(entry.get("connectorId") or "").strip())
        if pin is None:
            out.append(entry)
            continue
        current = _live_limit(entry)
        bound = {"recordGroups": list(pin.record_groups), "records": list(pin.records)}
        if current is not None:
            bound = await _intersection(graph, org_id, bound, current)
        if not any(bound.values()):
            continue
        entry.pop("filtersParsed", None)
        entry["filters"] = json.dumps(bound)
        out.append(entry)
    return out


async def _intersection(
    graph: IGraphDBProvider, org_id: str, pinned: dict[str, list[str]], live: Mapping[str, list[str]],
) -> dict[str, list[str]]:
    """The ids of either limit that lie inside the other, by place (main's ``SelectionScope``, no access)."""
    from app.harness.knowledge.scope_overlay import record_group_ids
    from app.modules.retrieval.selection_scope import (
        SelectionScope,
        resolve_selection_scope,
    )

    async def scope(limit: Mapping[str, list[str]]) -> SelectionScope:
        return await resolve_selection_scope(graph, "", org_id, limit, readable_only=False) or SelectionScope()

    pinned_scope, live_scope = await scope(pinned), await scope(live)
    groups_of = await record_group_ids(graph, list(dict.fromkeys([*pinned["records"], *live["records"]])))

    def inside(scope: SelectionScope, key: str, node_id: str) -> bool:
        return scope.admits({"id": node_id, "groupIds": groups_of.get(node_id, []) if key == "records" else []})

    return {
        key: list(dict.fromkeys([
            *(i for i in pinned[key] if inside(live_scope, key, i)),
            *(i for i in live.get(key, []) if inside(pinned_scope, key, i)),
        ]))
        for key in ("recordGroups", "records")
    }


def selection_refused(exc: Exception, *, agent_limit: bool) -> RunRefused:
    """Main's selection errors as a typed refusal: main's code and the words its routes send."""
    from app.modules.retrieval.selection_scope import selection_error

    code, message = selection_error(exc, agent_limit=agent_limit)  # type: ignore[arg-type]
    return RunRefused(code, message)


__all__ = ["bounded_agent_knowledge", "requested_filters", "selection_refused"]
