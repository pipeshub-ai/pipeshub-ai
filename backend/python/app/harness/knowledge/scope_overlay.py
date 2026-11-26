"""Publications on a scoped read by id (SYNC4 S11, MAINSTUDY B5).

Main's ``ids_within_scope`` admits a node when the turn's scope admits it *and* the reader can access it on their
own: a record a publication discloses is refused there even inside the scope. With publications on, the harness
lays them over the scope test: an id the scope admits by where it sits (the hierarchy, never access) passes on to
the read gate, which then serves it only through a live publication. Outside the scope nothing passes, whatever is
published: scope wins over publications; a turn with no scope reads as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.org_context import org_of

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.modules.retrieval.selection_scope import SelectionScope
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


async def published_within_scope(
    state: Mapping[str, Any],
    graph: IGraphDBProvider,
    user_key: str,
    org_id: str,
    record_ids: Sequence[str],
) -> set[str]:
    """Of ``record_ids``, those of ``org_id`` every scope of the turn admits by place; nothing when publications are
    off. The read gate still decides each one."""
    from app.agents.actions.knowledge_graph.ops.scope import (
        _picked_apps,  # pyright: ignore[reportPrivateUsage]
    )
    from app.harness.knowledge.service import KnowledgePublications
    from app.modules.retrieval.selection_scope import (
        SelectionScope,
        resolve_request_scopes,
    )

    ids = [i for i in dict.fromkeys(record_ids) if i]
    if not ids or not await KnowledgePublications(graph, getattr(graph, "config_service", None)).enabled():
        return set()
    filters = state.get("filters") or {}
    _, scopes = await resolve_request_scopes(graph, user_key, org_id, filters)
    picked = _picked_apps(filters)
    if picked is not None:
        scopes = [*scopes, SelectionScope(app_ids=picked)]
    return await within_scopes_by_place(graph, org_id, scopes, ids)


async def within_scopes_by_place(
    graph: IGraphDBProvider, org_id: str, scopes: Sequence[SelectionScope], record_ids: Sequence[str],
) -> set[str]:
    """Of ``record_ids``, those of ``org_id`` that every scope admits by where they sit (never by access)."""
    from app.config.constants.arangodb import CollectionNames

    ids = [i for i in dict.fromkeys(record_ids) if i]
    if not ids:
        return set()
    rows = await graph.get_nodes_by_field_in(
        CollectionNames.RECORDS.value, "id", ids, return_fields=["id", "orgId", "connectorId"], raise_on_error=True,
    )
    groups = await record_group_ids(graph, [str(row["id"]) for row in rows or [] if row.get("id")])
    return {
        str(row["id"])
        for row in rows or []
        if row.get("id") and org_of(row) == org_id
        and all(
            scope.admits({"id": row["id"], "connectorId": row.get("connectorId"), "groupIds": groups.get(row["id"], [])})
            for scope in scopes
        )
    }


async def record_group_ids(graph: IGraphDBProvider, record_ids: Sequence[str]) -> dict[str, list[str]]:
    """Each record's own record groups (its BELONGS_TO edges), the ``groupIds`` ``check_access`` rows carry, read
    from the hierarchy alone. A record whose edges cannot be read has none, so no group admits it."""
    import asyncio

    from app.config.constants.arangodb import CollectionNames

    prefix = f"{CollectionNames.RECORD_GROUPS.value}/"
    edges = await asyncio.gather(*(
        graph.get_edges_from_node(f"{CollectionNames.RECORDS.value}/{rid}", CollectionNames.BELONGS_TO.value)
        for rid in record_ids
    ))
    return {
        rid: [str(e["_to"]).removeprefix(prefix) for e in found or [] if str(e.get("_to") or "").startswith(prefix)]
        for rid, found in zip(record_ids, edges, strict=True)
    }


__all__ = ["published_within_scope", "record_group_ids", "within_scopes_by_place"]
