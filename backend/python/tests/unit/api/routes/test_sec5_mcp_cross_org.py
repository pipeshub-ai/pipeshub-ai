"""SEC5-3..SEC5-8: product MCP instance routes resolve an instance only within the caller's org.

One store holds three orgs (see ``sec5_mcp_world``). Every instance route of ``mcp_servers.py``
is a row; every caller replays org A's instance ids on every row. Another org's caller gets 404
(403 on an admin row when not an admin: the role gate answers before any lookup), the store is byte-identical
afterwards and the body names nothing of org A. Org A's own callers get the route's own answer, and under an
edition that shares instances with a child org, the child's callers get the same answer except on the rows
that change the instance, which stay the owner's.
"""

from __future__ import annotations

import copy
import inspect
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.routes import mcp_servers as server_routes
from app.core.org_context import OrgContext, bind
from tests.unit.api.routes.sec5_mcp_world import (
    A_HTTP,
    A_OAUTH,
    A_SECRET_NAME,
    A_SHARED,
    A_STDIO,
    B_HTTP,
    CALLERS,
    CURRENT_ORG,
    ORG_A,
    InheritingEdition,
    install_roles,
    seed,
)
from tests.unit.harness.mcp_hub.support import URL, FakeServer, hub_on, serve

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.unit.harness.credentials.support import DictConfig

PREFIX = "/api/v1/mcp-servers"
INSTANCE_BODY = {"name": "Renamed", "transport": "streamable_http", "authMode": "api_token", "url": URL}


@dataclass(frozen=True)
class Row:
    method: str
    path: str
    instance: str
    ok: int
    admin: bool = False
    mutates_instance: bool = False
    body: dict[str, Any] | None = None

    def concrete(self, agent_key: str) -> str:
        return self.path.format(instance_id=self.instance, agent_key=agent_key)


ROWS: dict[str, Row] = {
    "get instance": Row("GET", "/instances/{instance_id}", A_HTTP, 200, admin=True),
    "update instance": Row("PUT", "/instances/{instance_id}", A_HTTP, 200, admin=True, mutates_instance=True, body=INSTANCE_BODY),
    "delete instance": Row("DELETE", "/instances/{instance_id}", A_HTTP, 200, admin=True, mutates_instance=True),
    # Main's custom-server logos (#4430's follow-up): the people who may edit the server, inside its org.
    "set logo": Row("PUT", "/instances/{instance_id}/logo", A_HTTP, 400, admin=True, mutates_instance=True, body={"logo": "data:image/png;base64,AA=="}),
    "remove logo": Row("DELETE", "/instances/{instance_id}/logo", A_HTTP, 200, admin=True, mutates_instance=True),
    "authenticate": Row("POST", "/instances/{instance_id}/authenticate", A_HTTP, 200, body={"apiToken": "t"}),
    "update credentials": Row("PUT", "/instances/{instance_id}/credentials", A_HTTP, 200, body={"apiToken": "t"}),
    "remove credentials": Row("DELETE", "/instances/{instance_id}/credentials", A_HTTP, 200),
    "auto authenticate": Row("POST", "/instances/{instance_id}/auto-authenticate", A_SHARED, 200),
    "reauthenticate": Row("POST", "/instances/{instance_id}/reauthenticate", A_HTTP, 200),
    "oauth authorize": Row("GET", "/instances/{instance_id}/oauth/authorize", A_OAUTH, 200),
    # No credential record to refresh: 400 is the route's own answer, past the instance lookup.
    "oauth refresh": Row("POST", "/instances/{instance_id}/oauth/refresh", A_OAUTH, 400),
    "get oauth config": Row("GET", "/instances/{instance_id}/oauth-config", A_OAUTH, 200, admin=True),
    "update oauth config": Row(
        "PUT", "/instances/{instance_id}/oauth-config", A_OAUTH, 200, admin=True, body={"clientId": "c", "clientSecret": "s"}
    ),
    "tools": Row("GET", "/instances/{instance_id}/tools", A_HTTP, 200),
    # Main's per-user rules for the server's tools (#4256): the caller's own, never the instance.
    "my tool rules": Row("GET", "/instances/{instance_id}/my-tool-rules", A_HTTP, 200),
    "update my tool rules": Row("PUT", "/instances/{instance_id}/my-tool-rules", A_HTTP, 200, body={"tools": {}}),
    "agent authenticate": Row("POST", "/agents/{agent_key}/instances/{instance_id}/authenticate", A_HTTP, 200, body={"apiToken": "t"}),
    "agent update credentials": Row(
        "PUT", "/agents/{agent_key}/instances/{instance_id}/credentials", A_HTTP, 200, body={"apiToken": "t"}
    ),
    "agent remove credentials": Row("DELETE", "/agents/{agent_key}/instances/{instance_id}/credentials", A_HTTP, 200),
    "agent reauthenticate": Row("POST", "/agents/{agent_key}/instances/{instance_id}/reauthenticate", A_HTTP, 200),
    "agent oauth authorize": Row("GET", "/agents/{agent_key}/instances/{instance_id}/oauth/authorize", A_OAUTH, 200),
}


def expected(caller_name: str, row: Row, *, inheriting: bool) -> int:
    caller = CALLERS[caller_name]
    # Main's routes resolve the instance within the caller's org first: another org's reads as missing, admin or
    # not, and answers without the store.
    visible = caller.org_id == ORG_A or (inheriting and caller.org_id == "org-c")
    if not visible:
        return 404
    if row.admin and not caller.admin:
        return 403
    if caller.org_id == ORG_A:
        return row.ok
    return 403 if row.mutates_instance else row.ok


@pytest.fixture
def config() -> DictConfig:
    return seed()


def _app(config: DictConfig) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request: Request, call_next: Any) -> Any:  # noqa: ANN401 - Starlette's own types
        caller = CALLERS[request.headers["x-caller"]]
        request.state.user = caller.user()
        token = CURRENT_ORG.set(caller.org_id)
        # As main's auth middleware binds it (#4703): the routes that read `current_org_id()` see the caller's org.
        context = OrgContext(caller.org_id, request.state.user.get("userId"), "user", "jwt", "req-sec5")
        try:
            with bind(context):
                return await call_next(request)
        finally:
            CURRENT_ORG.reset(token)

    app.state.config_service = config
    app.state.mcp_registry = _NoTemplates()
    app.dependency_overrides[server_routes.require_mcp_enabled] = lambda: None
    app.include_router(server_routes.router)
    return app


class _NoTemplates:
    def get_template(self, _: str) -> None:
        return None


async def _no_agent_uses_it(*_: object) -> None:
    return None


@pytest.fixture
def client(config: DictConfig, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    _ = serve(monkeypatch, {URL: FakeServer()})
    _ = hub_on(monkeypatch)
    install_roles(monkeypatch)
    # Main's delete first asks the graph whether an agent uses the instance; no agent does here.
    monkeypatch.setattr(server_routes, "_assert_instance_not_in_use", _no_agent_uses_it)
    with TestClient(_app(config)) as test_client:
        yield test_client


def _send(client: TestClient, caller: str, row: Row) -> Any:  # noqa: ANN401
    path = PREFIX + row.concrete(CALLERS[caller].agent_key)
    return client.request(row.method, path, json=row.body, headers={"x-caller": caller})


def _assert_refusal_left_no_trace(response: Any, before: dict[str, Any], config: DictConfig) -> None:  # noqa: ANN401
    assert config.values == before, "a refused call changed the store"
    assert A_SECRET_NAME not in response.text and ORG_A not in response.text and URL not in response.text


SAME_ORG = ("A_ADMIN", "A_USER")
OTHER_ORG = ("B_ADMIN", "B_USER", "C_ADMIN", "C_USER")


@pytest.mark.parametrize("row_name", list(ROWS))
@pytest.mark.parametrize("caller", SAME_ORG + OTHER_ORG)
def test_sec5_3_every_instance_route_resolves_within_the_callers_org(
    client: TestClient, config: DictConfig, caller: str, row_name: str
) -> None:
    row = ROWS[row_name]
    before = copy.deepcopy(config.values)

    response = _send(client, caller, row)

    assert response.status_code == expected(caller, row, inheriting=False), response.text
    if response.status_code in (403, 404):
        _assert_refusal_left_no_trace(response, before, config)


@pytest.mark.parametrize("row_name", list(ROWS))
@pytest.mark.parametrize("caller", ("C_ADMIN", "C_USER", "B_ADMIN", "B_USER"))
def test_sec5_6_an_inherited_instance_is_shared_for_use_and_kept_for_its_owner(
    client: TestClient, config: DictConfig, monkeypatch: pytest.MonkeyPatch, caller: str, row_name: str
) -> None:
    InheritingEdition().install(monkeypatch)
    row = ROWS[row_name]
    before = copy.deepcopy(config.values)

    response = _send(client, caller, row)

    assert response.status_code == expected(caller, row, inheriting=True), response.text
    if response.status_code in (403, 404):
        assert config.values == before, "a refused call changed the store"


@pytest.mark.parametrize("caller", ("B_ADMIN", "B_USER"))
@pytest.mark.parametrize("row_name", [name for name, row in ROWS.items() if "{agent_key}" in row.path])
def test_sec5_4_own_agent_with_another_orgs_instance_is_not_found(
    client: TestClient, config: DictConfig, caller: str, row_name: str
) -> None:
    row = ROWS[row_name]
    before = copy.deepcopy(config.values)

    response = _send(client, caller, row)

    assert response.status_code == 404, response.text
    _assert_refusal_left_no_trace(response, before, config)


LISTS = {
    "list instances": "/instances",
    "my servers": "/my-mcp-servers",
    "agent servers": "/agents/{agent_key}",
}


def _listed_ids(body: dict[str, Any]) -> set[str]:
    entries: list[dict[str, Any]] = body["instances"]
    return {e["_id"] for e in entries}


@pytest.mark.parametrize("inheriting", [False, True], ids=["community", "inheriting"])
@pytest.mark.parametrize("list_name", list(LISTS))
@pytest.mark.parametrize("caller", ("A_ADMIN", "B_ADMIN", "C_ADMIN"))
def test_sec5_5_lists_show_only_what_the_callers_org_may_see(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caller: str, list_name: str, inheriting: bool
) -> None:
    if inheriting:
        InheritingEdition().install(monkeypatch)
    path = PREFIX + LISTS[list_name].format(agent_key=CALLERS[caller].agent_key)

    response = client.get(path, headers={"x-caller": caller})

    assert response.status_code == 200, response.text
    org_a = {A_HTTP, A_OAUTH, A_SHARED, A_STDIO}
    visible: dict[str, set[str]] = {
        "A_ADMIN": org_a,
        "B_ADMIN": {B_HTTP},
        "C_ADMIN": org_a if inheriting else set(),
    }
    assert _listed_ids(response.json()) == visible[caller]
    if caller == "B_ADMIN":
        assert A_SECRET_NAME not in json.dumps(response.json())


def test_sec5_7_every_instance_route_has_a_matrix_row() -> None:
    routes = {
        (method, route.path.removeprefix(PREFIX))
        for router in (server_routes.router,)
        for route in router.routes
        if isinstance(route, APIRoute) and "{instance_id}" in route.path
        for method in route.methods
    }
    rows = {(row.method, row.path) for row in ROWS.values()}

    assert routes - rows == set(), "instance routes with no cross-org row"
    assert rows - routes == set(), "rows naming no route"


def test_sec5_8_no_resolver_reads_an_instance_without_an_org() -> None:
    """Main scoped the resolvers themselves (#4003): each one takes the org it resolves within, so no caller can
    resolve an MCP instance without naming one (the property SEC5-8 once kept by routing callers through
    ``app.agents.mcp.instance_scope``)."""
    from app import edition_config
    from app.agents.mcp import service as mcp_service
    from app.api.routes import mcp_resolvers

    resolvers = [
        mcp_resolvers.get_mcp_instance,
        mcp_resolvers.load_mcp_instances,
        mcp_resolvers.resolve_mcp_instances_with_inheritance,
        edition_config.get_mcp_instance_resolved,
        edition_config.resolve_mcp_instances_with_inheritance,
        mcp_service.get_instance,
        mcp_service.load_org_instances,
        mcp_service.load_visible_instances,
    ]
    unscoped = [f"{f.__module__}.{f.__name__}" for f in resolvers if "org_id" not in inspect.signature(f).parameters]
    assert unscoped == []
    required = [
        f"{f.__module__}.{f.__name__}"
        for f in resolvers
        if inspect.signature(f).parameters["org_id"].default is not inspect.Parameter.empty
    ]
    assert required == [], "the org is never optional"
