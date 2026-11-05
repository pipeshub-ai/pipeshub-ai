"""One in-memory fake per port, for unit tests and for wiring a resolver without a graph."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .ports import AppFacts, Principal, ScopeNode, StampApp, StampNode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass
class InMemoryHierarchyView:
    org_id: str
    groups_by_app: Mapping[str, frozenset[str]] = field(default_factory=lambda: dict[str, frozenset[str]]())
    scopes: tuple[ScopeNode, ...] = ()
    facts_by_app: Mapping[str, AppFacts] = field(default_factory=lambda: dict[str, AppFacts]())
    pending: frozenset[str] = frozenset()
    failure: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())  # (kind, app) of each structure read

    async def record_group_ids(self, org_id: str, app_id: str) -> frozenset[str]:
        self._check(org_id)
        self.calls.append(("groups", app_id))
        return self.groups_by_app.get(app_id, frozenset()) if org_id == self.org_id else frozenset()

    async def scope_nodes(self, org_id: str, app_id: str) -> tuple[ScopeNode, ...]:
        self._check(org_id)
        self.calls.append(("scopes", app_id))
        return tuple(s for s in self.scopes if s.connector_id == app_id) if org_id == self.org_id else ()

    async def app_facts(self, org_id: str, app_id: str) -> AppFacts:
        self._check(org_id)
        return self.facts_by_app.get(app_id, AppFacts(False, "")) if org_id == self.org_id else AppFacts(False, "")

    async def pending_scopes(self, org_id: str) -> frozenset[str]:
        self._check(org_id)
        return self.pending if org_id == self.org_id else frozenset()

    def _check(self, org_id: str) -> None:
        if self.failure is not None:
            raise self.failure


@dataclass
class InMemoryPrincipalView:
    """`principals` and `admitted` are keyed by (org, user). `admitted` is what the graph check would say."""

    principals: Mapping[tuple[str, str], Principal] = field(default_factory=lambda: dict[tuple[str, str], Principal]())
    admitted: Mapping[tuple[str, str], frozenset[str]] = field(default_factory=lambda: dict[tuple[str, str], frozenset[str]]())
    failure: Exception | None = None
    calls: list[int] = field(default_factory=lambda: list[int]())  # size of each `readable` request

    async def principal(self, org_id: str, user_key: str) -> Principal:
        if self.failure is not None:
            raise self.failure
        found = self.principals.get((org_id, user_key), Principal(frozenset(), frozenset()))
        return replace(found, context=user_key)  # `readable` finds the viewer's answers by it

    async def readable(self, org_id: str, who: Principal, node_ids: frozenset[str]) -> frozenset[str]:
        if self.failure is not None:
            raise self.failure
        self.calls.append(len(node_ids))
        return node_ids & self.admitted.get((org_id, str(who.context)), frozenset())


@dataclass
class InMemoryStampGraph:
    """A `StampGraph` over dicts. `reads` counts the `nodes` calls and `failure` makes every read raise."""

    org_id: str
    apps: Mapping[str, StampApp] = field(default_factory=lambda: dict[str, StampApp]())
    node_map: Mapping[str, StampNode] = field(default_factory=lambda: dict[str, StampNode]())
    failure: Exception | None = None
    reads: list[int] = field(default_factory=lambda: list[int]())
    app_reads: int = 0

    async def app(self, org_id: str, app_id: str) -> StampApp:
        self._check()
        self.app_reads += 1
        return self.apps.get(app_id, StampApp("", False, False)) if org_id == self.org_id else StampApp("", False, False)

    async def nodes(self, org_id: str, ids: frozenset[str]) -> dict[str, StampNode]:
        self._check()
        self.reads.append(len(ids))
        return {i: self.node_map[i] for i in ids if i in self.node_map} if org_id == self.org_id else {}

    async def subtree_vrids(self, org_id: str, ids: Sequence[str]) -> list[str]:
        self._check()
        if org_id != self.org_id:
            return []
        seen = {i for i in ids if i in self.node_map}
        frontier = list(seen)
        for _ in range(50):
            frontier = [k for k, n in self.node_map.items() if k not in seen and any(p in frontier for p in n.parents)]
            if not frontier:
                break
            seen.update(frontier)
        return sorted({n.vrid for k in seen if (n := self.node_map[k]).kind == "record" and n.vrid})

    async def record_page(self, org_id: str, app_id: str, after: str | None, limit: int) -> list[str]:
        self._check()
        ids = sorted(i for i, n in self.node_map.items() if n.kind == "record" and n.connector_id == app_id)
        return [i for i in ids if after is None or i > after][:limit] if org_id == self.org_id else []

    def _check(self) -> None:
        if self.failure is not None:
            raise self.failure
