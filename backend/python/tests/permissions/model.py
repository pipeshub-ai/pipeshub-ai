"""The typed permission world: who exists, what exists, who holds what.

One storage-neutral description that every layer is checked against. `graph.py`
compiles it into the node and edge lists the graph providers store, and the YAML
scenario DSL (`scenario.py`) serialises it. Standard library only.

The shapes mirror the graph rather than any one connector, so a world can also
describe graphs no connector writes (a hierarchy cycle, an inherit edge to a
non-parent). The connector-specific knowledge lives in the scenario files, as
comments that cite the writer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

PrincipalKind = Literal["user", "group", "role", "team"]
NodeKind = Literal["app", "group", "record"]

HIERARCHY_RELATIONS = ("PARENT_CHILD", "ATTACHMENT")
ORG_ID = "org"  # the reserved id of the world's Organization node in grants


@dataclass(frozen=True)
class Principal:
    """Something that can hold a grant or be a member: a user, group, role or team."""

    id: str
    kind: PrincipalKind = "user"
    org_member: bool = True  # users only: a BELONGS_TO edge to the organization
    active: bool = True  # users only: User.isActive; the access check does not read it
    deleted: bool = False  # groups only: Group.isDeleted; the access check does not read it


@dataclass(frozen=True)
class Parent:
    """A NODE_RELATION edge from `id` down to the node that lists it."""

    id: str
    rel: str = "PARENT_CHILD"  # ATTACHMENT is hierarchy too; anything else is a link


@dataclass(frozen=True)
class Node:
    """An app, a record group or a record, with the edges that leave it."""

    id: str
    kind: NodeKind
    connector: str | None = None  # id of the owning app; None on an app
    rule: str | None = None  # accessRule as stored; None means absent (reads OPEN)
    parents: tuple[Parent, ...] = ()
    inherits: tuple[str, ...] = ()  # INHERIT_PERMISSIONS edges (need not be a parent)
    belongs: tuple[str, ...] = ()  # BELONGS_TO edges to a group or the app
    deleted: bool = False
    placeholder: bool = False
    active: bool = True  # apps only: App.isActive; the gate does not read it
    hide_children: bool = False  # groups only: hideChildren; a display flag, not read for access
    model: str | None = None  # permissionModel: APP_LEVEL, RECORD_GROUP_LEVEL
    app_type: str = "DRIVE"  # apps only; KB opens everything
    origin: str | None = "CONNECTOR"  # records only; None means absent
    vrid: str | None = None  # virtual record id (records)
    indexing: str = "COMPLETED"
    has_indexed_copy: bool = False
    org: str | None = None  # label of a foreign org; None means the world's own
    # The combining rule a projector should stamp this node with: "container" when its access equals its record
    # group's, else its own rule ("intersection", "override", "union"). Documentation for tests, not read by the oracle.
    stamp: str | None = None


@dataclass(frozen=True)
class Grant:
    """A PERMISSION edge from a principal to a node. Role is ignored for reading."""

    grantee: str
    node: str
    type: str = "USER"  # an org's grant counts only as ORG or ORGANIZATION
    role: str = "READER"


@dataclass(frozen=True)
class Membership:
    """A principal's membership edge to a group, role or team (one level only)."""

    member: str
    group: str
    type: str = "USER"  # only USER counts as membership
    edge: str = "PERMISSION"  # BELONGS_TO from a user is not membership


@dataclass(frozen=True)
class Gate:
    """A principal's relation to an app. ORG_APP_RELATION never gates."""

    who: str
    app: str
    via: str = "USER_APP_RELATION"


@dataclass(frozen=True)
class Link:
    """A user's linked source account inside one connector (AUTHENTICATED_AS)."""

    user: str
    source: str
    connector: str


@dataclass(frozen=True)
class World:
    principals: tuple[Principal, ...] = ()
    nodes: tuple[Node, ...] = ()
    grants: tuple[Grant, ...] = ()
    memberships: tuple[Membership, ...] = ()
    gates: tuple[Gate, ...] = ()
    links: tuple[Link, ...] = ()

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"no node {node_id!r}")

    def users(self) -> tuple[str, ...]:
        return tuple(p.id for p in self.principals if p.kind == "user")

    def with_node(self, node: Node) -> World:
        """Replace the node with the same id."""
        return replace(self, nodes=tuple(node if n.id == node.id else n for n in self.nodes))


MUTATION_KINDS = (
    "grant_added",  # node, ref=grantee, value=edge type (default USER)
    "grant_removed",  # node, ref=grantee
    "member_added",  # node=group, ref=member
    "member_removed",  # node=group, ref=member
    "rule_set",  # node, value=accessRule ("" removes it)
    "inherit_set",  # node, ref=parent, value="true" | "false"
    "moved",  # node, ref=new parent, value=new BELONGS_TO group ("" keeps it)
    "deleted",  # node
    "user_removed",  # node=principal: also leaves the org (BELONGS_TO), memberships, grants, gates, links
    "user_deactivated",  # node=principal: the source drops the person; only the group syncs react (memberships go)
    "gate_withdrawn",  # node=principal, ref=app: the connector takes its own app away from the person (nothing else changes)
)
STRUCTURAL_KINDS = frozenset({"inherit_set", "moved"})


@dataclass(frozen=True)
class Mutation:
    """One change at the source, applied to a world by `mutations.apply`."""

    kind: str
    node: str = ""
    ref: str = ""
    value: str = ""


class WorldBuilder:
    """Accumulates a world in code, edge by edge. Mirrors the YAML form; `build()` freezes it."""

    def __init__(self) -> None:
        self._principals: list[Principal] = []
        self._nodes: dict[str, Node] = {}
        self._grants: list[Grant] = []
        self._memberships: list[Membership] = []
        self._gates: list[Gate] = []
        self._links: list[Link] = []

    def principal(self, pid: str, kind: PrincipalKind = "user", org_member: bool = True) -> str:
        self._principals.append(Principal(pid, kind, org_member))
        return pid

    def node(self, node: Node) -> str:
        self._nodes[node.id] = node
        return node.id

    def _edit(self, node_id: str, **changes: object) -> None:
        self._nodes[node_id] = replace(self._nodes[node_id], **changes)

    def parent(self, parent: str, child: str, rel: str = "PARENT_CHILD") -> None:
        self._edit(child, parents=(*self._nodes[child].parents, Parent(parent, rel)))

    def inherit(self, child: str, parent: str) -> None:
        self._edit(child, inherits=(*self._nodes[child].inherits, parent))

    def belong(self, child: str, group: str) -> None:
        self._edit(child, belongs=(*self._nodes[child].belongs, group))

    def grant(self, grantee: str, node: str, kind: str = "USER") -> None:
        self._grants.append(Grant(grantee, node, kind))

    def member(self, member: str, group: str, kind: str = "USER", edge: str = "PERMISSION") -> None:
        self._memberships.append(Membership(member, group, kind, edge))

    def gate(self, who: str, app: str, via: str = "USER_APP_RELATION") -> None:
        self._gates.append(Gate(who, app, via))

    def link(self, user: str, source: str, connector: str) -> None:
        self._links.append(Link(user, source, connector))

    def build(self) -> World:
        return World(
            tuple(self._principals),
            tuple(self._nodes.values()),
            tuple(self._grants),
            tuple(self._memberships),
            tuple(self._gates),
            tuple(self._links),
        )
