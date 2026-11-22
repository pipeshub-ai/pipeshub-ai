"""The connectors page lists under a connector's access limit (``accessLimit``): a member sees a limited connector only when its
limit admits them, an admin sees every one with its ``accessLimit`` so the page can mark it. Driven through the real routes and
``ConnectorRegistry`` over a graph stand-in that applies the limit the way the three graph queries do; the queries themselves are
checked on real databases (``tests/integration/graph_permissions/test_connector_instances.py``,
``tests/integration/graph_db/postgres/test_connectors_teams.py``, ``tests/permissions/test_access_limit_reach.py``)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import user_access_router
from app.connectors.api.connector_resolvers import authorize_connector_stats
from app.connectors.api.router import (
    get_active_agent_instances,
    get_active_connector_instances,
    get_configured_connector_instances,
    get_connector_instances,
    get_inactive_connector_instances,
)
from app.connectors.core.registry.connector_registry import Connector, ConnectorRegistry

ORG = "org-1"
# Every app is the org's (its org edge), active, configured and on for agents, so every list could show it.
APPS: list[dict[str, Any]] = [
    {"_key": "open", "accessLimit": None},
    {"_key": "byAnn", "accessLimit": "CREATOR", "createdBy": "u-ann"},
    {"_key": "orgWide", "accessLimit": "ORG"},
    {"_key": "junk", "accessLimit": "SOMEONE"},                       # any other value admits nobody
    {"_key": "foreign", "accessLimit": "ORG", "orgId": "org-2"},      # linked to this org, stamped another: admitted under neither
    {"_key": "elsewhereCreator", "accessLimit": "CREATOR", "createdBy": "u-ann", "orgId": "org-2"},
]
MEMBERS = {("u-ann", ORG), ("u-bob", ORG), ("u-eve", "org-2")}        # u-eve's membership edge is to another org


def _app(doc: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Notion", "name": f"Name of {doc['_key']}", "scope": "team", "orgId": ORG, "createdBy": "u-admin",
            "isActive": True, "isConfigured": True, "isAgentActive": True, **doc}


class _LimitGraph:
    """The org's connector instances with the access-limit rule of the graph queries (``_aql_access_limit_filter``,
    ``_cypher_access_limit_keeps``, PostgreSQL ``_limit_keeps``): unlimited, or of the request's org and admitted by its limit."""

    def __init__(self) -> None:
        self.apps = [_app(d) for d in APPS]
        self.asked: list[bool] = []

    def _keeps(self, app: dict[str, Any], user_id: str, org_id: str) -> bool:
        limit = app.get("accessLimit")
        if limit is None:
            return True
        if app.get("orgId") != org_id:
            return False
        if limit == "CREATOR":
            return app.get("createdBy") == user_id
        return limit == "ORG" and (user_id, app["orgId"]) in MEMBERS

    def _listed(self, user_id: str, org_id: str, apply_access_limit: bool) -> list[dict[str, Any]]:
        self.asked.append(apply_access_limit)
        return [dict(a) for a in self.apps if not apply_access_limit or self._keeps(a, user_id, org_id)]

    async def get_user_connector_instances(self, collection: str, user_id: str, org_id: str, team_scope: str, personal_scope: str,
                                           transaction: str | None = None, *, apply_access_limit: bool = False) -> list[dict]:
        return self._listed(user_id, org_id, apply_access_limit)

    async def get_filtered_connector_instances(self, *, user_id: str, org_id: str, skip: int = 0, limit: int = 20,
                                               apply_access_limit: bool = False, **_: object) -> tuple[list[dict], int]:
        rows = self._listed(user_id, org_id, apply_access_limit)
        return rows[skip:skip + limit], len(rows)


@pytest.fixture
def graph() -> _LimitGraph:
    return _LimitGraph()


@pytest.fixture
def registry(graph: _LimitGraph) -> ConnectorRegistry:
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    reg = ConnectorRegistry(container)

    @Connector(name="Notion", app_group="Notion", supported_auth_types="OAUTH")
    class _Notion:
        pass
    reg.register_connector(_Notion)
    data_store = MagicMock()
    data_store.graph_provider = graph
    container.data_store = AsyncMock(return_value=data_store)
    return reg


def _request(registry: ConnectorRegistry, user_id: str, *, admin: bool = False) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": user_id, "orgId": ORG, "role": "admin" if admin else "member"}
    request.app.state.connector_registry = registry
    request.app.container.logger.return_value = logging.getLogger("test")
    return request


async def _active(request: MagicMock) -> list[dict]:
    return (await get_active_connector_instances(request))["connectors"]


async def _inactive(request: MagicMock, graph: _LimitGraph) -> list[dict]:
    for app in graph.apps:
        app["isActive"] = False
    return (await get_inactive_connector_instances(request))["connectors"]


async def _all(request: MagicMock) -> list[dict]:
    return (await get_connector_instances(request, scope=None, page=1, limit=50, search=None, is_authenticated=None,
                                          is_active=None, connector_type=None))["connectors"]


async def _configured(request: MagicMock) -> list[dict]:
    return (await get_configured_connector_instances(request, scope=None, page=1, limit=50, search=None))["connectors"]["connectors"]


async def _agents_active(request: MagicMock) -> list[dict]:
    return (await get_active_agent_instances(request, scope=None, page=1, limit=50, search=None))["connectors"]


LISTS = ["active", "inactive", "all", "configured", "agents_active"]


async def _list(name: str, request: MagicMock, graph: _LimitGraph) -> dict[str, str | None]:
    rows = {
        "active": lambda: _active(request),
        "inactive": lambda: _inactive(request, graph),
        "all": lambda: _all(request),
        "configured": lambda: _configured(request),
        "agents_active": lambda: _agents_active(request),
    }[name]
    return {c["_key"]: c["accessLimit"] for c in await rows()}


@pytest.mark.parametrize("name", LISTS)
@pytest.mark.parametrize(("user", "expected"), [
    ("u-bob", {"open", "orgWide"}),                    # a member: CREATOR is ann's, junk admits nobody, the others are another org's
    ("u-ann", {"open", "byAnn", "orgWide"}),           # the creator also sees hers, under this org only
    ("u-eve", {"open"}),                               # a member edge to another org admits to no ORG connector here
])
async def test_a_member_sees_a_limited_connector_only_when_its_limit_admits_them(
    registry: ConnectorRegistry, graph: _LimitGraph, name: str, user: str, expected: set[str],
) -> None:
    listed = await _list(name, _request(registry, user), graph)
    assert set(listed) == expected
    assert graph.asked and all(graph.asked)


@pytest.mark.parametrize("name", LISTS)
async def test_an_admin_sees_every_connector_with_its_limit(registry: ConnectorRegistry, graph: _LimitGraph, name: str) -> None:
    listed = await _list(name, _request(registry, "u-admin", admin=True), graph)
    assert listed == {a["_key"]: a["accessLimit"] for a in APPS}
    assert graph.asked and not any(graph.asked)


async def test_an_admin_who_is_also_the_creator_still_sees_the_rest(registry: ConnectorRegistry, graph: _LimitGraph) -> None:
    assert set(await _list("active", _request(registry, "u-ann", admin=True), graph)) == {a["_key"] for a in APPS}


@pytest.mark.parametrize(("user", "admin", "expected"), [
    ("u-bob", False, {"open": None, "orgWide": "ORG"}),
    ("u-ann", False, {"open": None, "byAnn": "CREATOR", "orgWide": "ORG"}),
    ("u-admin", True, {a["_key"]: a["accessLimit"] for a in APPS}),
])
async def test_the_per_user_connector_list_follows_the_same_rule(graph: _LimitGraph, user: str, admin: bool,
                                                                expected: dict[str, str | None]) -> None:
    request = MagicMock()
    request.state.user = {"userId": user, "role": "admin" if admin else "member"}
    graph_provider = MagicMock()
    graph_provider.get_filtered_connector_instances = graph.get_filtered_connector_instances
    config = AsyncMock()
    config.get_config.return_value = {"sync": {"accessMode": "per_user"}}
    with patch.object(user_access_router, "current_org_id", return_value=ORG), \
            patch.object(user_access_router, "_SUPPORTED_TYPES", frozenset({"Notion"})), \
            patch.object(user_access_router, "get_user_or_503",
                         new=AsyncMock(return_value={"id": "n1", "userId": user, "email": "x@acme.test", "orgId": ORG})), \
            patch.object(user_access_router, "_config_service", return_value=config), \
            patch.object(user_access_router, "get_connection", new=AsyncMock(return_value=None)), \
            patch.object(user_access_router, "get_invite", new=AsyncMock(return_value=None)):
        result = await user_access_router.list_my_per_user_connectors(request, graph_provider=graph_provider)
    assert {c["connectorId"]: c["accessLimit"] for c in result["connectors"]} == expected
    assert graph.asked == [not admin]


@pytest.mark.parametrize(("user", "admin", "allowed"), [
    ("u-bob", False, False),       # a member the limit refuses: the same 404 as a connector that does not exist
    ("u-eve", False, False),
    ("u-ann", False, True),        # the creator, as today
    ("u-admin", True, True),       # an admin, as today
])
@pytest.mark.parametrize("key", ["byAnn", "orgWide", "junk"])
async def test_stats_of_a_limited_connector_are_for_an_admin_or_its_creator(
    graph: _LimitGraph, user: str, admin: bool, allowed: bool, key: str,
) -> None:
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    reg = ConnectorRegistry(container)

    @Connector(name="Notion", app_group="Notion", supported_auth_types="OAUTH")
    class _Notion:
        pass
    reg.register_connector(_Notion)
    app = {**next(a for a in graph.apps if a["_key"] == key), "createdBy": "u-ann"}
    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(return_value=app)
    data_store = MagicMock()
    data_store.graph_provider = graph_provider
    container.data_store = AsyncMock(return_value=data_store)
    request = MagicMock()
    request.state.user = {"userId": user, "orgId": ORG, "role": "admin" if admin else "member"}
    if allowed:
        await authorize_connector_stats(request, graph_provider, reg, key, ORG)
    else:
        with pytest.raises(HTTPException) as refused:
            await authorize_connector_stats(request, graph_provider, reg, key, ORG)
        assert refused.value.status_code == 404
