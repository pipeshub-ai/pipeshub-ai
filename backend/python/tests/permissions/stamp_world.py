"""A `World` as a `StampGraph`: what the projector reads, built from the world's nodes with no oracle involved."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.permissions.fakes import InMemoryStampGraph
from app.services.permissions.ports import StampApp, StampNode

from .model import HIERARCHY_RELATIONS

if TYPE_CHECKING:
    from .model import World

ORG = "ab" * 12  # 24 hex characters: the form every store accepts


def stamp_graph(world: World, prefix: str = "", vrids: dict[str, str] | None = None, org: str = ORG) -> InMemoryStampGraph:
    """`prefix` namespaces every id, so many worlds can share one graph; `vrids` gives records their virtual record id."""
    ids = {n.id for n in world.nodes if n.org is None}
    granted = {g.node for g in world.grants}
    nodes: dict[str, StampNode] = {}
    apps: dict[str, StampApp] = {}
    for n in world.nodes:
        if n.org is not None:  # another organisation's node is not in this one's graph
            continue
        if n.kind == "app":
            group_level = any(m.kind == "group" and m.connector == n.id and m.model == "RECORD_GROUP_LEVEL" for m in world.nodes)
            apps[prefix + n.id] = StampApp(n.app_type, n.model == "APP_LEVEL" or n.app_type == "KB", group_level)
            nodes[prefix + n.id] = StampNode(prefix + n.id, "app", prefix + n.id)
            continue
        vrid = (vrids or {}).get(n.id) or n.vrid
        nodes[prefix + n.id] = StampNode(
            prefix + n.id, n.kind, prefix + (n.connector or ""),
            tuple(sorted(prefix + p.id for p in n.parents if p.rel in HIERARCHY_RELATIONS and p.id in ids)),
            frozenset(prefix + i for i in n.inherits), n.rule, n.deleted, n.placeholder, n.id in granted,
            n.kind == "group" and n.model == "RECORD_GROUP_LEVEL",
            tuple(sorted(prefix + b for b in n.belongs if b in ids and world.node(b).kind == "group")), prefix + vrid if vrid else None,
            any(b in ids and world.node(b).kind == "app" and b == n.connector for b in n.belongs))
    return InMemoryStampGraph(org, apps, nodes)


class WorldGraph:
    """A graph provider over the worlds added to it: the reads `membership.py` and the projector make."""

    def __init__(self) -> None:
        self.stamps = InMemoryStampGraph(ORG)

    def add(self, world: World, prefix: str, vrids: dict[str, str] | None = None) -> None:
        g = stamp_graph(world, prefix, vrids)
        self.stamps.apps = {**self.stamps.apps, **g.apps}
        self.stamps.node_map = {**self.stamps.node_map, **g.node_map}

    def permission_stamp_graph(self) -> InMemoryStampGraph:
        return self.stamps

    def docs(self, vrid: str) -> list[dict]:
        return [self.doc(i) for i, n in self.stamps.node_map.items() if n.kind == "record" and n.vrid == vrid and not n.deleted]

    def doc(self, key: str) -> dict | None:
        n = self.stamps.node_map.get(key)
        if n is None:
            return None
        return {"_key": key, "orgId": ORG, "connectorId": n.connector_id, "recordGroupId": n.groups[0] if n.groups else None,
                "virtualRecordId": n.vrid, "isDeleted": n.deleted}

    async def get_records_by_virtual_record_id(self, vrid: str, *args: object, **kwargs: object) -> list[str]:
        return [d["_key"] for d in self.docs(vrid)]

    async def get_document(self, key: str, collection: str, *args: object, **kwargs: object) -> dict | None:
        return self.doc(key)

    async def get_edges_from_node(self, node_id: str, edge_collection: str, *args: object, **kwargs: object) -> list[dict]:
        key = node_id.split("/", 1)[1]
        n = self.stamps.node_map.get(key)
        return [{"_from": node_id, "_to": f"recordGroups/{g}"} for g in (n.groups if n else ())]
