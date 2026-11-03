"""The two read-only ports the resolver depends on, and the values that cross them.

`permissions` imports no graph, vector or connector code: adapters (`adapters/`) implement these over Neo4j
and ArangoDB, `fakes.py` over plain dicts. Dependency direction: retrieval -> permissions <- adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ScopeNode:
    """A record or record group with an access list of its own."""

    id: str
    connector_id: str
    #: Passing it takes more than its parent's access: its rule is RESTRICTED (or unknown, which fails closed),
    #: or it does not inherit from a hierarchy parent. Deleted nodes with children are restricted too.
    restricted: bool
    #: The graph check can admit it only if the viewer holds a direct grant on it: it is deleted, or its rule is
    #: not OPEN/STRICT, or it inherits from none of its hierarchy parents, and no other way in applies (the app
    #: opens everything, or a record group of the app declares its own readers). The resolver does not ask the
    #: graph about such a node unless the viewer holds a grant on it; when unsure, leave it False.
    needs_grant: bool = False
    #: Restricted by its rule (RESTRICTED or unknown) or by deletion, not merely because it does not inherit a parent.
    #: Such a node hides what is below it under every combining rule; one that only fails to inherit (a Drive file in
    #: My Drive) hides nothing under Union. Defaults to True: when unsure, list it.
    rule_restricted: bool = True


@dataclass(frozen=True)
class AppFacts:
    #: APP_LEVEL permission model or a KB: membership of the app admits by itself.
    opens: bool
    #: The connector type, e.g. "CONFLUENCE"; picks the combining rule (`rules.py`).
    connector_type: str
    #: A record group of the app declares its own readers (record-group-level permission model). Under the
    #: Intersection rule the path filter is not exact for such an app, so the resolver leaves the app out.
    record_group_level: bool = False


@dataclass(frozen=True)
class Principal:
    tokens: frozenset[str]
    #: Apps of the organisation the viewer passes the connector gate for.
    gated_app_ids: frozenset[str]
    #: Records and record groups of those apps the viewer (or a group, role, team or organisation they hold, or a
    #: source account they linked) holds a direct grant on. Empty when unknown, which only costs more questions.
    granted_ids: frozenset[str] = frozenset()
    #: Whatever the view needs back in `readable` (the access it resolved once for this viewer). Opaque to the resolver.
    context: object | None = None


class HierarchyView(Protocol):
    """The organisation's structure. Independent of the viewer, so it needs no cache of its own."""

    async def record_group_ids(self, org_id: str, app_id: str) -> frozenset[str]:
        """Every record group of this app (readable or not)."""
        ...

    async def scope_nodes(self, org_id: str, app_id: str) -> tuple[ScopeNode, ...]:
        """Every scope of this app (readable or not)."""
        ...

    async def app_facts(self, org_id: str, app_id: str) -> AppFacts:
        """Whether the app opens everything, and its connector type. An unknown app opens nothing."""
        ...

    async def pending_scopes(self, org_id: str) -> frozenset[str]:
        """Scopes whose subtree is being rewritten (empty until the pending registry exists)."""
        ...


class PrincipalView(Protocol):
    """What the graph says about one viewer."""

    async def principal(self, org_id: str, user_key: str) -> Principal: ...

    async def readable(self, org_id: str, who: Principal, node_ids: frozenset[str]) -> frozenset[str]:
        """The subset of `node_ids` the graph check admits to this viewer (`who` is what `principal` returned)."""
        ...


@dataclass(frozen=True)
class StampNode:
    """A record, record group or app, with what a stamper needs to know about it. Viewer-independent. An app is a node
    only because a hierarchy may reach it (it then belongs on a path); it is never a scope."""

    id: str
    kind: str  # "record" | "group" | "app"
    connector_id: str
    #: Hierarchy parents (PARENT_CHILD and ATTACHMENT edges into it), by id. Parents that do not exist are left out.
    parents: tuple[str, ...] = ()
    #: The parents it inherits access from (INHERIT_PERMISSIONS edges).
    inherits: frozenset[str] = frozenset()
    #: accessRule as stored; None reads OPEN.
    rule: str | None = None
    deleted: bool = False
    placeholder: bool = False
    #: At least one PERMISSION edge into it.
    granted: bool = False
    #: A record group that declares its own readers (permissionModel RECORD_GROUP_LEVEL).
    group_level: bool = False
    #: The record groups it belongs to (BELONGS_TO), or for a group its parent groups. Apps are not listed.
    groups: tuple[str, ...] = ()
    vrid: str | None = None
    #: A BELONGS_TO edge to the app itself (what makes a record a member of an app that opens everything).
    belongs_app: bool = False


@dataclass(frozen=True)
class StampApp:
    connector_type: str
    #: APP_LEVEL permission model or a KB: membership of the app admits everything in it.
    opens: bool
    #: Some record group of the app is RECORD_GROUP_LEVEL.
    has_group_level: bool


class StampGraph(Protocol):
    """What a stamper reads. Read-only, independent of any viewer, and not cached by the implementation."""

    async def app(self, org_id: str, app_id: str) -> StampApp: ...

    async def nodes(self, org_id: str, ids: frozenset[str]) -> dict[str, StampNode]:
        """The records and record groups of this organisation among `ids`; ids that do not exist are absent."""
        ...

    async def record_page(self, org_id: str, app_id: str, after: str | None, limit: int) -> list[str]:
        """Up to `limit` record ids of the app, ordered, strictly after `after` (deleted ones included). A failed read raises."""
        ...
