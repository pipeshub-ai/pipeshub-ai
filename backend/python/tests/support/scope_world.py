"""A small knowledge hierarchy behind main's one access check (#4255), for the SYNC4 scope tests.

Only the backend half is faked: ``_kh_v3_accessible_rows`` (which nodes a user reaches, as rows), and
``get_selection_nodes`` (what a selection covers, from the hierarchy alone). ``check_access`` and every wrapper over
it are the interface's own, bound onto the graph double, so a test exercises the decision the product makes.

The world::

    app-a ─ grp-1 ─ fld-1 (a folder) ─ rec-in   (v-in)
                  └ rec-out                       (v-out)
          grp-2 ─ rec-g2                          (v-g2)
    app-b ─ grp-b ─ rec-b                         (v-b)

``user-1`` (graph key ``ukey-1``) reaches every node until ``revoke`` takes a node and everything under it away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MethodType
from typing import Any

from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

ORG = "org-1"
USER = "user-1"
USER_KEY = "ukey-1"

APP_A, APP_B = "app-a", "app-b"
GRP_1, GRP_2, GRP_B = "grp-1", "grp-2", "grp-b"
FOLDER = "fld-1"
REC_IN, REC_OUT, REC_G2, REC_B = "rec-in", "rec-out", "rec-g2", "rec-b"
VRID = {REC_IN: "v-in", REC_OUT: "v-out", REC_G2: "v-g2", REC_B: "v-b"}


@dataclass
class ScopeWorld:
    #: node id -> (parent id or None, kind, app id)
    nodes: dict[str, tuple[str | None, str, str]] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)
    #: every (node_ids, vrids) the access rows were asked for
    asked: list[tuple[frozenset[str], frozenset[str]]] = field(default_factory=list)

    @classmethod
    def build(cls) -> ScopeWorld:
        world = cls()
        for app in (APP_A, APP_B):
            world.nodes[app] = (None, "app", app)
        world.nodes[GRP_1] = (APP_A, "group", APP_A)
        world.nodes[GRP_2] = (APP_A, "group", APP_A)
        world.nodes[GRP_B] = (APP_B, "group", APP_B)
        world.nodes[FOLDER] = (GRP_1, "folder", APP_A)
        world.nodes[REC_IN] = (FOLDER, "record", APP_A)
        world.nodes[REC_OUT] = (GRP_1, "record", APP_A)
        world.nodes[REC_G2] = (GRP_2, "record", APP_A)
        world.nodes[REC_B] = (GRP_B, "record", APP_B)
        return world

    def revoke(self, node_id: str) -> None:
        self.revoked.add(node_id)

    def _ancestors(self, node_id: str) -> list[str]:
        out: list[str] = []
        parent = self.nodes[node_id][0]
        while parent is not None:
            out.append(parent)
            parent = self.nodes[parent][0]
        return out

    def _reachable(self, node_id: str) -> bool:
        return node_id in self.nodes and not ({node_id, *self._ancestors(node_id)} & self.revoked)

    def _row(self, node_id: str) -> dict[str, Any]:
        _, kind, app = self.nodes[node_id]
        return {
            "id": node_id,
            "vrid": VRID.get(node_id),
            "connectorId": app,
            "indexingStatus": "COMPLETED",
            "isInternal": False,
            "groupIds": [a for a in self._ancestors(node_id) if self.nodes[a][1] == "group"],
            "kind": kind,
        }

    async def accessible_rows(
        self, user_key: str, org_id: str, node_ids: list[str], vrids: list[str], *,
        access: object = None, transaction: object = None,
    ) -> list[dict[str, Any]]:
        self.asked.append((frozenset(node_ids), frozenset(vrids)))
        if user_key != USER_KEY or org_id != ORG:
            return []
        by_vrid = {v: r for r, v in VRID.items()}
        wanted = {n for n in node_ids if n in self.nodes} | {by_vrid[v] for v in vrids if v in by_vrid}
        return [self._row(n) for n in sorted(wanted) if self._reachable(n)]

    def _under(self, node_id: str) -> list[str]:
        return [n for n in self.nodes if node_id in self._ancestors(n)]

    async def selection_nodes(
        self, org_id: str, *, group_ids: list[str], record_ids: list[str], exact_record_ids: list[str], limit: int,
    ) -> dict[str, Any]:
        groups: dict[str, str] = {}
        records: dict[str, tuple[str | None, str]] = {}
        for g in group_ids:
            if self.nodes.get(g, (None, ""))[1] != "group":
                continue
            groups[g] = self.nodes[g][2]
            for n in self._under(g):
                kind = self.nodes[n][1]
                if kind == "group":
                    groups[n] = self.nodes[n][2]
        for r in record_ids:
            if self.nodes.get(r, (None, ""))[1] not in ("record", "folder"):
                continue
            for n in (r, *self._under(r)):
                records[n] = (VRID.get(n), self.nodes[n][2])
        for r in exact_record_ids:
            if self.nodes.get(r, (None, ""))[1] == "record":
                records[r] = (VRID.get(r), self.nodes[r][2])
        return {
            "groups": [{"id": g, "connectorId": app} for g, app in groups.items()],
            "records": [{"id": r, "vrid": vrid, "connectorId": app} for r, (vrid, app) in records.items()],
        }

    async def nodes_by_field_in(
        self, collection: str, field_name: str, values: list[str], *,
        return_fields: list[str] | None = None, raise_on_error: bool = False, **_: object,
    ) -> list[dict[str, Any]]:
        if collection == "apps":
            return [{"id": v, "vectorMembershipBackfilled": True} for v in values if self.nodes.get(v, (None, ""))[1] == "app"]
        if collection == "records" and field_name == "virtualRecordId":
            by_vrid = {v: r for r, v in VRID.items()}
            return [{"id": by_vrid[v], "virtualRecordId": v, "orgId": ORG, "origin": "CONNECTOR",
                     "connectorId": self.nodes[by_vrid[v]][2]} for v in values if v in by_vrid]
        if collection == "records" and field_name == "id":
            return [{"id": v, "orgId": ORG, "origin": "CONNECTOR", "connectorId": self.nodes[v][2]}
                    for v in values if v in self.nodes]
        return []

    def edges_from_node(self, shape: str) -> Any:  # noqa: ANN401
        """``get_edges_from_node`` as each backend answers it: BELONGS_TO from a record to each record group it sits
        in, and the app edge a KB record also carries. Neo4j builds ``_from``/``_to`` from labels around the
        relationship's properties; ArangoDB returns the stored edge document."""

        async def edges(node_id: str, edge_collection: str, transaction: object = None) -> list[dict[str, Any]]:
            collection, _, key = node_id.partition("/")
            if edge_collection != "belongsTo" or collection != "records" or key not in self.nodes:
                return []
            targets = [f"recordGroups/{g}" for g in self._row(key)["groupIds"]] + [f"apps/{self.nodes[key][2]}"]
            if shape == "neo4j":
                return [{"createdAtTimestamp": 1, "_from": node_id, "_to": t} for t in targets]
            return [
                {"_key": f"e{i}", "_id": f"belongsTo/e{i}", "_rev": "r", "_from": node_id, "_to": t,
                 "entityType": "GROUP"}
                for i, t in enumerate(targets)
            ]

        return edges

    async def accessible_vrids(self, user_id: str, org_id: str, **_: object) -> dict[str, str]:
        """The old enumeration (``get_accessible_virtual_record_ids``) the publications arm still starts from."""
        if user_id != USER or org_id != ORG:
            return {}
        return {VRID[r]: r for r in VRID if self._reachable(r)}

    async def user_by_user_id(self, user_id: str | None = None, *_: object, **__: object) -> dict[str, Any] | None:
        return {"_key": USER_KEY, "id": USER_KEY, "userId": USER, "email": "user@corp.example"} if user_id == USER else None

    def install(self, graph: Any, *, shape: str = "neo4j") -> Any:  # noqa: ANN401
        """Put the world behind ``graph`` (a double of ``IGraphDBProvider``): the backend half from here, the
        access check and its wrappers from the interface itself. ``shape`` picks which backend's edge documents
        ``get_edges_from_node`` answers with."""
        import logging

        graph.logger = logging.getLogger("scope_world")
        graph.get_edges_from_node = self.edges_from_node(shape)
        graph._kh_v3_accessible_rows = self.accessible_rows
        graph.get_selection_nodes = self.selection_nodes
        graph.get_nodes_by_field_in = self.nodes_by_field_in
        graph.get_user_by_user_id = self.user_by_user_id
        graph.get_accessible_virtual_record_ids = self.accessible_vrids
        for name in (
            "check_access",
            "filter_accessible_record_ids",
            "filter_accessible_virtual_record_ids",
            "filter_nodes_with_permission_role",
        ):
            setattr(graph, name, MethodType(getattr(IGraphDBProvider, name), graph))
        return graph


__all__ = [
    "APP_A", "APP_B", "FOLDER", "GRP_1", "GRP_2", "GRP_B", "ORG", "REC_B", "REC_G2", "REC_IN", "REC_OUT",
    "USER", "USER_KEY", "VRID", "ScopeWorld",
]
