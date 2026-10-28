"""Record-link helpers shared by the SQL connectors.

Foreign keys between tables are ``recordLinks`` edges between their records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_RECORDS = CollectionNames.RECORDS.value
_RECORD_LINKS = CollectionNames.RECORD_LINKS.value

NOT_EDGE_PROPERTIES = frozenset({
    "_key", "_id", "_rev", "from_id", "to_id", "from_collection", "to_collection",
})


def normalize_edge(doc: dict) -> dict:
    """Accept generic or raw Arango/Neo4j edge shapes; expose from_id/to_id."""
    if "from_id" in doc and "to_id" in doc:
        return doc
    out = dict(doc)
    if "_from" in out:
        parts = str(out["_from"]).split("/", 1)
        if len(parts) == 2:
            out["from_collection"] = parts[0]
            out["from_id"] = parts[1]
        out.pop("_from", None)
    if "_to" in out:
        parts = str(out["_to"]).split("/", 1)
        if len(parts) == 2:
            out["to_collection"] = parts[0]
            out["to_id"] = parts[1]
        out.pop("_to", None)
    return out


async def move_incoming_relations(
    graph_provider: IGraphDBProvider, *, from_record_id: str, to_record_id: str,
) -> int:
    """Point the record relations that end at one record at another instead.

    For a stand-in a table record replaces: deleting it takes the foreign
    keys of the tables that reference it, and those tables are not synced
    again until they change. Returns the number of edges moved.
    """
    moved: list[dict[str, Any]] = []
    # A failed read must raise: the caller deletes the stand-in next, and its
    # foreign keys with it.
    incoming = await graph_provider.get_edges_to_node(
        f"{_RECORDS}/{from_record_id}", _RECORD_LINKS, raise_on_error=True,
    ) or []
    for raw in incoming:
        edge = normalize_edge(raw)
        source = str(edge.get("from_id") or "")
        if not source or source == to_record_id:
            continue
        moved.append({
            **{k: v for k, v in edge.items() if k not in NOT_EDGE_PROPERTIES},
            "_from": f"{_RECORDS}/{source}",
            "_to": f"{_RECORDS}/{to_record_id}",
        })
    if moved:
        await graph_provider.batch_upsert_node_relations(moved)
    return len(moved)
