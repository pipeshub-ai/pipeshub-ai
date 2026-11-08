"""Compile a `World` into the node and edge lists the graph providers store.

The list shape is the one `tests/integration/graph_permissions/loaders.py`
loads, so the same graph feeds the oracle, Neo4j and ArangoDB. Every id is
namespaced by a prefix and every org by `org-<prefix>`, so many worlds can share
one store without touching each other. Standard library only.
"""

from __future__ import annotations

from typing import TypedDict

from .model import HIERARCHY_RELATIONS, ORG_ID, Node, Principal, World

Scalar = str | int | bool | None
TS = 1_700_000_000_000


class NodeDict(TypedDict):
    kind: str
    id: str
    props: dict[str, Scalar]


class _EdgeBase(TypedDict):
    type: str
    to: str
    props: dict[str, Scalar]


# `from` is a keyword, so the edge type is declared with the functional syntax.
EdgeDict = TypedDict("EdgeDict", {"type": str, "from": str, "to": str, "props": dict[str, Scalar]})


class Graph(TypedDict):
    nodes: list[NodeDict]
    edges: list[EdgeDict]


class Namespace:
    """Maps world-local ids to store ids and back."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.org = f"org-{prefix}"
        self.org_node = f"{prefix}-orgnode"

    def id(self, local: str) -> str:
        return self.org_node if local == ORG_ID else f"{self.prefix}-{local}"

    def local(self, stored: str) -> str:
        head = f"{self.prefix}-"
        return stored[len(head):] if stored.startswith(head) else stored

    def org_of(self, node: Node) -> str:
        return self.org if node.org is None else f"{self.org}-{node.org}"


def compile_world(world: World, prefix: str) -> Graph:
    ns = Namespace(prefix)
    ids = {n.id for n in world.nodes} | {p.id for p in world.principals}
    first_app = next((n.id for n in world.nodes if n.kind == "app"), None)
    nodes: list[NodeDict] = [
        {"kind": "Organization", "id": ns.org_node, "props": {
            "id": ns.org_node, "name": "Org", "accountType": "enterprise", "isActive": True}}
    ]
    edges: list[EdgeDict] = []

    def ref(local: str) -> str:
        return ns.id(local)

    def edge(kind: str, src: str, dst: str, **props: Scalar) -> None:
        edges.append({"type": kind, "from": ref(src), "to": ref(dst),
                      "props": {"createdAtTimestamp": TS, **props}})

    for p in world.principals:
        nodes.append(_principal_node(ns, p, first_app))
        if p.kind == "user" and p.org_member:
            edge("BELONGS_TO", p.id, ORG_ID, entityType="ORGANIZATION")
    for n in world.nodes:
        nodes.append(_node(ns, n, ids))
        for parent in n.parents:
            edge("NODE_RELATION", parent.id, n.id, relationshipType=parent.rel)
        for target in n.inherits:
            edge("INHERIT_PERMISSIONS", n.id, target)
        for target in n.belongs:
            edge("BELONGS_TO", n.id, target)
    for g in world.grants:
        edge("PERMISSION", g.grantee, g.node, type=g.type, role=g.role)
    for m in world.memberships:
        extra: dict[str, Scalar] = {"type": m.type, "role": "READER"} if m.edge == "PERMISSION" else {}
        edge(m.edge, m.member, m.group, **extra)
    for gate in world.gates:
        sync: dict[str, Scalar] = {"syncState": "COMPLETED", "lastSyncUpdate": TS} if gate.via == "USER_APP_RELATION" else {}
        edge(gate.via, gate.who, gate.app, **sync)
    for link in world.links:
        edge("AUTHENTICATED_AS", link.user, link.source, connectorId=ref(link.connector), orgId=ns.org)
    return {"nodes": nodes, "edges": edges}


def _principal_node(ns: Namespace, p: Principal, first_app: str | None) -> NodeDict:
    pid, kind = p.id, p.kind
    sid = ns.id(pid)
    org = {"orgId": ns.org}
    if kind == "user":
        props: dict[str, Scalar] = {**org, "email": f"{pid}@{ns.prefix}.example", "fullName": pid, "userId": sid}
        if not p.active:
            props["isActive"] = False
        return {"kind": "User", "id": sid, "props": {"id": sid, **props}}
    if kind == "role":
        connector = ns.id(first_app) if first_app else "x"
        return {"kind": "Role", "id": sid, "props": {
            "id": sid, **org, "name": pid, "externalRoleId": f"ext-{pid}", "connectorName": "DRIVE",
            "connectorId": connector, "createdAtTimestamp": TS}}
    label = "Teams" if kind == "team" else "Group"
    group_props: dict[str, Scalar] = {"id": sid, **org, "name": pid}
    if p.deleted:
        group_props["isDeleted"] = True
    return {"kind": label, "id": sid, "props": group_props}


def _node(ns: Namespace, n: Node, ids: set[str]) -> NodeDict:
    sid = ns.id(n.id)
    base: dict[str, Scalar] = {"id": sid, "orgId": ns.org_of(n)}
    connector = None if n.connector is None else (ns.id(n.connector) if n.connector in ids else n.connector)
    if n.kind == "app":
        props = {**base, "name": n.id, "type": n.app_type, "appGroup": "Group", "scope": "team",
                 "createdAtTimestamp": TS, "isActive": n.active}
        if n.model:
            props["permissionModel"] = n.model
        return {"kind": "App", "id": sid, "props": props}
    if n.kind == "group":
        props = {**base, "groupName": n.id, "groupType": "CONFLUENCE_SPACES", "connectorName": "CONFLUENCE",
                 "createdAtTimestamp": TS, "accessRule": n.rule, "connectorId": connector,
                 "isInternal": False, "hideChildren": n.hide_children}
        if n.model:
            props["permissionModel"] = n.model
        if n.deleted:
            props["isDeleted"] = True
        if n.placeholder:
            props["isPlaceholder"] = True
        return {"kind": "RecordGroup", "id": sid, "props": props}
    props = {**base, "recordName": n.id, "externalRecordId": f"ext-{n.id}", "recordType": "FILE",
             "origin": n.origin, "connectorId": connector, "createdAtTimestamp": TS,
             "virtualRecordId": ns.id(n.vrid or f"v-{n.id}"), "isDeleted": n.deleted,
             "isPlaceholder": n.placeholder, "isInternal": False, "indexingStatus": n.indexing}
    if n.rule is not None:
        props["accessRule"] = n.rule
    if n.has_indexed_copy:
        props["hasIndexedCopy"] = True
    return {"kind": "Record", "id": sid, "props": props}


def is_hierarchy(rel: str) -> bool:
    return rel in HIERARCHY_RELATIONS
