"""`note_org_structure_change` reads and marks each organisation's connectors inside that organisation, so it also works with the org
context enforced and none bound by the caller."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.core.org_context import ambient_org
from app.services.permissions.mode import ENV_SCOPE_FIELDS
from app.services.permissions.structure_generation import note_org_structure_change


class _Graph:
    def __init__(self) -> None:
        self.marked: list[str] = []
        self.get_all_orgs = AsyncMock(side_effect=self._orgs)

    async def _orgs(self, **kwargs: object) -> list[dict]:
        ambient_org(None, site="get_all_orgs") if False else None  # the enumeration is cross-org: a system context is enough
        return [{"_key": "o1"}, {"_key": "o2"}]

    async def get_org_apps(self, org: str, **kwargs: object) -> list[dict]:
        assert ambient_org(None, site="get_org_apps") == org  # raises under enforcement when no org (or another org) is bound
        return [{"_key": f"app-{org}"}]

    async def increment_node_field(self, key: str, collection: str, field: str, transaction: str | None = None) -> int:
        assert ambient_org(None, site="increment_node_field") in ("o1", "o2")
        self.marked.append(key)
        return 1


async def test_every_connector_of_every_organisation_is_marked_with_the_context_enforced_and_none_bound(monkeypatch) -> None:
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    graph = _Graph()
    await note_org_structure_change(graph, None)
    assert sorted(graph.marked) == ["app-o1", "app-o2"]
