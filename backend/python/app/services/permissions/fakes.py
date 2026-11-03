"""One in-memory fake per port, for unit tests and for wiring a resolver without a graph."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .ports import AppFacts, Principal, ScopeNode

if TYPE_CHECKING:
    from collections.abc import Mapping


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
