"""Writes that mint or hold credentials, point stored credentials at another host, or run a
local command take an interactive session. A caller on an OAuth access token, a personal
access token or a client_credentials token (all ``isOAuth``) is refused with the same 403 as
the Node API, whatever its scopes; a session token (what Node forwards for a signed-in user)
gets through.
"""

import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI, Request, Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.edition_config  # noqa: F401  (load the edition seams before any router)
from app.api.middlewares.auth import (
    SESSION_ONLY_DETAIL,
    AuthRejected,
    is_set_value,
    refuse_delegated_caller,
    require_interactive_session,
    require_interactive_session_except,
)

SESSION = {"userId": "u1", "orgId": "o1", "role": "admin"}
DELEGATED = {
    "PAT or OAuth access token": {**SESSION, "isOAuth": True, "oauthScopes": ["connector:write", "mcp:write"]},
    "client_credentials token": {**SESSION, "isOAuth": True, "oauthScopes": ["connector:write"], "principalKind": "app"},
}


def _app(*routes) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _user(request: Request, call_next) -> Response:
        kind = request.headers.get("x-test-user")
        request.state.user = SESSION if kind == "session" else DELEGATED.get(kind or "")
        return await call_next(request)

    for path, deps in routes:
        @app.put(path, dependencies=[Depends(d) for d in deps])
        async def _handler() -> dict:
            return {"ran": True}

    return app


_ALLOWED = {"sync": True, "filters": True, "config": ["sync", "filters"]}


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(("/whole", [require_interactive_session]), ("/allow", [require_interactive_session_except(_ALLOWED, ("auth", "config.auth"))])))


@pytest.mark.parametrize("kind", list(DELEGATED))
def test_route_wide_refuses_every_delegated_token(client, kind) -> None:
    r = client.put("/whole", headers={"x-test-user": kind}, json={})
    assert r.status_code == 403
    assert r.json()["detail"] == SESSION_ONLY_DETAIL


def test_route_wide_lets_a_session_through(client) -> None:
    r = client.put("/whole", headers={"x-test-user": "session"}, json={})
    assert r.status_code == 200 and r.json() == {"ran": True}


@pytest.mark.parametrize("body", [
    {"auth": {"clientSecret": "s"}},
    {"auth": {"values": {"instanceUrl": "https://evil.example.invalid"}}},
    {"baseUrl": "https://evil.example.invalid"},
    {"auth": "a bare string"},
    # Keys no name list would have caught: they link another OAuth app or are simply new.
    {"auth": {"oauthConfigId": "other-app"}},
    {"oauthConfigId": "other-app"},
    {"aKeyNoListHasSeen": 1},
    {"auth": {"aKeyNoListHasSeen": 1}},
    {"auth": {"oauthConfigId": None}},
    {"clientId": "x", "clientSecret": "y"},
    {"baseUrl": ""},
    {"oauthConfigId": 0},
    {"flag": False},
    {"config": {"auth": {"oauthConfigId": "other-app"}}},
    {"config": "a bare string"},
    {"sync": {}, "other": [None]},
    # Present is enough, even empty, unless the route lists the key as one a caller sends empty.
    {"authConfig": {}},
    {"other": []},
    {"other": None},
    {"oauthConfigId": None},
    {"baseUrl": None},
    {"config": {"other": {}}},
    {"auth": {"x": 1}},
    {"auth": [1]},
    {"config": {"auth": {"x": 1}}},
])
@pytest.mark.parametrize("kind", list(DELEGATED))
def test_allowlist_refuses_every_key_outside_it(client, kind, body) -> None:
    r = client.put("/allow", headers={"x-test-user": kind}, json=body)
    assert r.status_code == 403
    assert r.json()["detail"] == SESSION_ONLY_DETAIL


@pytest.mark.parametrize("body", [
    {},
    {"sync": {"selectedStrategy": "MANUAL"}},
    {"filters": {"sync": {}}},
    {"config": {"sync": {"x": 1}, "filters": {}}},
    # The keys the route lists as ones a real caller sends empty.
    {"auth": {}},
    {"auth": None},
    {"auth": []},
    {"config": {"auth": {}}},
    {"config": []},
])
def test_allowlist_lets_a_token_send_allowed_keys_and_empty_ones(client, body) -> None:
    r = client.put("/allow", headers={"x-test-user": "PAT or OAuth access token"}, json=body)
    assert r.status_code == 200


def test_allowlist_lets_a_session_send_anything(client) -> None:
    r = client.put("/allow", headers={"x-test-user": "session"}, json={"auth": {"clientSecret": "s"}, "baseUrl": "https://x"})
    assert r.status_code == 200


def test_allowlist_refuses_a_body_that_is_not_an_object(client) -> None:
    r = client.put("/allow", headers={"x-test-user": "PAT or OAuth access token"}, json=["x"])
    assert r.status_code == 403


def test_set_value_rule_ignores_key_names() -> None:
    for v in [{"oauthConfigId": "x"}, {"a": {"b": 1}}, {"k": None}, [1], "s", "", 0, False, True]:
        assert is_set_value(v), v
    for v in [None, {}, []]:
        assert not is_set_value(v), v


def test_stdio_mcp_server_setup_is_refused_to_a_token_only() -> None:
    from app.agents.mcp.models import MCPTransport
    from app.api.routes.mcp_servers import _refuse_delegated_stdio

    def req(user) -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(user=user))

    with pytest.raises(AuthRejected) as e:
        _refuse_delegated_stdio(req(DELEGATED["PAT or OAuth access token"]), SimpleNamespace(transport=MCPTransport.STDIO))
    assert e.value.status_code == 403 and e.value.detail == SESSION_ONLY_DETAIL
    _refuse_delegated_stdio(req(SESSION), SimpleNamespace(transport=MCPTransport.STDIO))
    _refuse_delegated_stdio(req(DELEGATED["PAT or OAuth access token"]), SimpleNamespace(transport=next(t for t in MCPTransport if t != MCPTransport.STDIO)))
    refuse_delegated_caller(req(SESSION))


# The routes in the class, by router. A route that loses its guard fails here.
_ROUTE_WIDE = {
    ("app.api.routes.api_toolsets", "router"): [("POST", "/instances"), ("PUT", "/instances/{instance_id}")],
    ("app.api.routes.mcp_servers", "router"): [("PUT", "/instances/{instance_id}/oauth-config")],
    # An OAuth app holds a client secret and the URLs a grant is sent to; connectors link to it.
    ("app.connectors.api.router", "router"): [
        ("POST", "/api/v1/oauth/{connector_type}"),
        ("PUT", "/api/v1/oauth/{connector_type}/{config_id}"),
        ("DELETE", "/api/v1/oauth/{connector_type}/{config_id}"),
    ],
    ("app.api.routes.toolsets", "router"): [
        ("PUT", "/oauth-configs/{toolset_type}/{oauth_config_id}"),
        ("DELETE", "/oauth-configs/{toolset_type}/{oauth_config_id}"),
    ],
}
# Routes where a token may send only these keys; each list is the whole allowlist, so adding a
# key to a route's allowlist fails here until it is reviewed.
_ALLOWLISTED = {
    ("app.connectors.api.router", "router"): [
        ("PUT", "/api/v1/connectors/{connector_id}/config", {"sync": True, "filters": True}),
        ("PUT", "/api/v1/connectors/{connector_id}/config/auth", {}),
        ("POST", "/api/v1/connectors/registry/{connector_type}/test-connection", {"authType": True, "connectorId": True}),
        ("POST", "/api/v1/connectors/", {
            "connectorType": True, "instanceName": True, "scope": True, "authType": True, "config": ["sync", "filters"],
        }),
    ],
    ("app.api.routes.toolsets", "router"): [
        ("POST", "/instances", {"instanceName": True, "toolsetType": True, "authType": True, "scope": True}),
        ("PUT", "/instances/{instance_id}", {"instanceName": True}),
    ],
    ("app.api.routes.mcp_servers", "router"): [
        ("POST", "/instances", {"name": True, "description": True, "typeId": True, "scope": True, "transport": True, "authMode": True, "useAdminAuth": True, "scopes": True}),
        ("PUT", "/instances/{instance_id}", {"name": True, "description": True, "typeId": True, "scope": True, "transport": True, "authMode": True, "useAdminAuth": True, "scopes": True}),
    ],
}


# Off-list keys a route lets a token send empty, because a real caller does (see the comment at
# the route). Every other route refuses an off-list key whenever it is present.
_EMPTY_ALLOWED = {
    ("app.connectors.api.router", "router"): [
        ("PUT", "/api/v1/connectors/{connector_id}/config", []),
        ("PUT", "/api/v1/connectors/{connector_id}/config/auth", []),
        ("POST", "/api/v1/connectors/registry/{connector_type}/test-connection", ["auth"]),
        ("POST", "/api/v1/connectors/", ["config.auth"]),
    ],
    ("app.api.routes.toolsets", "router"): [
        ("POST", "/instances", []),
        ("PUT", "/instances/{instance_id}", []),
    ],
    ("app.api.routes.mcp_servers", "router"): [
        ("POST", "/instances", []),
        ("PUT", "/instances/{instance_id}", []),
    ],
}


def _route(module: str, attr: str, method: str, path: str) -> APIRoute:
    router = getattr(importlib.import_module(module), attr)
    matches = [r for r in router.routes if isinstance(r, APIRoute) and r.path.endswith(path) and method in r.methods]
    assert len(matches) == 1, f"{method} {path}: {len(matches)} routes"
    return matches[0]


@pytest.mark.parametrize("module,attr,method,path", [(m, a, me, p) for (m, a), rs in _ROUTE_WIDE.items() for me, p in rs])
def test_route_wide_session_guard_is_declared(module, attr, method, path) -> None:
    route = _route(module, attr, method, path)
    assert any(d.call is require_interactive_session for d in route.dependant.dependencies), f"{method} {path}"


@pytest.mark.parametrize("module,attr,method,path,allowed", [(m, a, me, p, f) for (m, a), rs in _ALLOWLISTED.items() for me, p, f in rs])
def test_allowlist_guard_is_declared_with_exactly_these_keys(module, attr, method, path, allowed) -> None:
    route = _route(module, attr, method, path)
    declared = [d.call.session_only_allowlist for d in route.dependant.dependencies if hasattr(d.call, "session_only_allowlist")]
    assert declared == [allowed], f"{method} {path}"


@pytest.mark.parametrize("module,attr,method,path,empty", [(m, a, me, p, e) for (m, a), rs in _EMPTY_ALLOWED.items() for me, p, e in rs])
def test_allowlist_guard_lists_exactly_these_empty_keys(module, attr, method, path, empty) -> None:
    route = _route(module, attr, method, path)
    declared = [d.call.session_only_empty_allowed for d in route.dependant.dependencies if hasattr(d.call, "session_only_allowlist")]
    assert declared == [empty], f"{method} {path}"


def test_a_token_issued_with_config_write_loses_it() -> None:
    from app.api.middlewares.auth import _normalize_regular_payload

    payload = _normalize_regular_payload(
        {"userId": "u1", "orgId": "o1", "tokenType": "oauth", "scope": "config:write kb:read", "client_id": "c1"},
        "token",
    )
    assert payload["isOAuth"] is True
    assert payload["oauthScopes"] == ["kb:read"]


# Over HTTP, through the real routers: a token that sends only an OAuth app link, or any key
# outside a route's allowlist, is refused; a session is not.
_C = "app.connectors.api.router"
_T = "app.api.routes.toolsets"
_TOOLSET = {"instanceName": "a", "toolsetType": "jira", "authType": "OAUTH"}
_A = "app.api.routes.api_toolsets"
_A_BODY = {
    "name": "a", "baseUrl": "https://evil.example.invalid", "operations": ["op"],
    "oauthClientId": "attacker-client", "oauthClientSecret": "attacker-secret",
}
_M = "app.api.routes.mcp_servers"
_MCP = {"name": "m", "transport": "streamable_http", "authMode": "api_token"}
_HTTP_ROUTES = [
    # The two bodies from review: an empty `auth` beside a top-level app link on the config save,
    # and a create whose `config.auth` carries credentials.
    (_C, "PUT", "/api/v1/connectors/c1/config", {"auth": {}, "oauthConfigId": "other-app"}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "config": {"auth": {"clientId": "attacker-client", "clientSecret": "attacker-secret"}}}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "authType": "OAUTH", "config": {"auth": {"oauthConfigId": "other-app", "authType": "OAUTH"}}}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"auth": {"oauthConfigId": "other-app"}}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"oauthConfigId": "other-app"}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"aKeyNoListHasSeen": "x"}),
    (_C, "PUT", "/api/v1/connectors/c1/config/auth", {"auth": {"oauthConfigId": "other-app"}}),
    (_C, "PUT", "/api/v1/connectors/c1/config/auth", {"aKeyNoListHasSeen": "x"}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "config": {"auth": {"oauthConfigId": "other-app"}}}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "oauthConfigId": "other-app"}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "aKeyNoListHasSeen": "x"}),
    (_C, "POST", "/api/v1/connectors/registry/JIRA/test-connection", {"auth": {"oauthConfigId": "other-app"}}),
    (_C, "POST", "/api/v1/connectors/registry/JIRA/test-connection", {"auth": {}, "aKeyNoListHasSeen": "x"}),
    (_C, "POST", "/api/v1/oauth/JIRA", {"oauthInstanceName": "app", "config": {"clientId": "x"}}),
    (_C, "PUT", "/api/v1/oauth/JIRA/cfg1", {"oauthInstanceName": "renamed"}),
    (_T, "POST", "/api/v1/toolsets/instances", {**_TOOLSET, "oauthConfigId": "other-app"}),
    (_T, "POST", "/api/v1/toolsets/instances", {**_TOOLSET, "authConfig": {"clientId": "x"}}),
    # The review's body: credentials as top-level keys, which the handler turns into an OAuth app.
    (_T, "POST", "/api/v1/toolsets/instances", {**_TOOLSET, "clientId": "attacker-client", "clientSecret": "attacker-secret"}),
    (_T, "POST", "/api/v1/toolsets/instances", {**_TOOLSET, "aKeyNoListHasSeen": "x"}),
    (_T, "PUT", "/api/v1/toolsets/instances/i1", {"oauthConfigId": "other-app"}),
    (_T, "PUT", "/api/v1/toolsets/instances/i1", {"clientId": "x", "clientSecret": "y"}),
    (_T, "PUT", "/api/v1/toolsets/instances/i1", {"aKeyNoListHasSeen": "x"}),
    # Present but empty is refused too: an empty authConfig replaces a stored one with nothing, and
    # an empty `auth` on the auth-only save clears the stored credentials of the connector.
    (_C, "PUT", "/api/v1/connectors/c1/config/auth", {"auth": {}}),
    (_C, "PUT", "/api/v1/connectors/c1/config/auth", {"auth": None}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"auth": {}}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"auth": None, "sync": {}}),
    (_T, "PUT", "/api/v1/toolsets/instances/i1", {"instanceName": "n", "authConfig": {}}),
    (_T, "PUT", "/api/v1/toolsets/instances/i1", {"instanceName": "n", "oauthConfigId": None}),
    (_T, "POST", "/api/v1/toolsets/instances", {**_TOOLSET, "authConfig": {}}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"oauthConfigId": None, "sync": {}}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"baseUrl": None}),
    (_C, "PUT", "/api/v1/connectors/c1/config/auth", {"auth": {}, "baseUrl": None}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "auth": {}}),
    (_T, "PUT", "/api/v1/toolsets/oauth-configs/slack/cfg1", {"authConfig": {"clientId": "x"}}),
    (_C, "DELETE", "/api/v1/oauth/JIRA/cfg1", {}),
    (_T, "DELETE", "/api/v1/toolsets/oauth-configs/slack/cfg1", {}),
    # An API toolset names its host and OAuth app on create as on edit.
    (_A, "POST", "/api/v1/api-toolsets/instances", _A_BODY),
    (_A, "PUT", "/api/v1/api-toolsets/instances/i1", _A_BODY),
    # An MCP server's URLs are where its credentials go; its OAuth app holds a secret.
    (_M, "POST", "/api/v1/mcp-servers/instances", {**_MCP, "url": "https://evil.example.invalid/mcp"}),
    (_M, "POST", "/api/v1/mcp-servers/instances", {**_MCP, "authMode": "oauth", "authorizationUrl": "https://evil.example.invalid/a", "tokenUrl": "https://evil.example.invalid/t"}),
    (_M, "POST", "/api/v1/mcp-servers/instances", {**_MCP, "aKeyNoListHasSeen": "x"}),
    (_M, "PUT", "/api/v1/mcp-servers/instances/i1", {**_MCP, "url": "https://evil.example.invalid/mcp"}),
    (_M, "PUT", "/api/v1/mcp-servers/instances/i1", {**_MCP, "headerName": "X-Api-Key"}),
    (_M, "PUT", "/api/v1/mcp-servers/instances/i1/oauth-config", {"clientId": "attacker-client", "clientSecret": "attacker-secret"}),
]
_ALLOWED_FOR_A_TOKEN = [
    (_C, "PUT", "/api/v1/connectors/c1/config", {"sync": {"selectedStrategy": "MANUAL"}}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"filters": {"sync": {}}}),
    (_C, "PUT", "/api/v1/connectors/c1/config", {"sync": {"oauthConfigId": "other"}}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "config": {"auth": {}, "sync": {}}}),
    (_C, "POST", "/api/v1/connectors/", {"connectorType": "JIRA", "instanceName": "j", "config": {"sync": {}}}),
    (_C, "POST", "/api/v1/connectors/registry/JIRA/test-connection", {"auth": {}, "connectorId": "c1"}),
    (_T, "POST", "/api/v1/toolsets/instances", {**_TOOLSET, "scope": "personal"}),
    (_T, "PUT", "/api/v1/toolsets/instances/i1", {"instanceName": "renamed"}),
    # A catalog server, whose template fixes its URLs.
    (_M, "POST", "/api/v1/mcp-servers/instances", {**_MCP, "typeId": "github", "scope": "personal"}),
    (_M, "PUT", "/api/v1/mcp-servers/instances/i1", {**_MCP, "name": "renamed", "description": "d"}),
]


def _http_client(module: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _user(request: Request, call_next) -> Response:
        kind = request.headers.get("x-test-user")
        request.state.user = SESSION if kind == "session" else DELEGATED.get(kind or "")
        return await call_next(request)

    mod = importlib.import_module(module)
    for gate in ("_require_enabled", "_require_mcp_enabled"):  # feature flags read the config store
        if hasattr(mod, gate):
            app.dependency_overrides[getattr(mod, gate)] = lambda: None
    app.include_router(mod.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("module,method,path,body", _HTTP_ROUTES)
@pytest.mark.parametrize("kind", list(DELEGATED))
def test_a_token_cannot_link_or_create_an_oauth_app(kind, module, method, path, body) -> None:
    r = _http_client(module).request(method, path, headers={"x-test-user": kind}, json=body)
    assert r.status_code == 403 and r.json()["detail"] == SESSION_ONLY_DETAIL


@pytest.mark.parametrize("module,method,path,body", _HTTP_ROUTES)
def test_a_session_is_not_refused_by_the_guard(module, method, path, body) -> None:
    # This app has no container, so the handler itself may fail; the session got past the guard.
    r = _http_client(module).request(method, path, headers={"x-test-user": "session"}, json=body)
    assert SESSION_ONLY_DETAIL not in r.text


@pytest.mark.parametrize("module,method,path,body", _ALLOWED_FOR_A_TOKEN)
def test_a_token_can_still_change_other_settings(module, method, path, body) -> None:
    r = _http_client(module).request(method, path, headers={"x-test-user": "PAT or OAuth access token"}, json=body)
    assert SESSION_ONLY_DETAIL not in r.text


def _raw(method: str, path: str, module: str, raw: bytes, content_type: str = "application/json", user: str = "PAT or OAuth access token") -> httpx.Response:
    return _http_client(module).request(method, path, content=raw, headers={"content-type": content_type, "x-test-user": user})


def _refused(r) -> bool:
    return r.status_code == 403 and SESSION_ONLY_DETAIL in r.text


# Parser and routing differentials: the guard and the handler read the body the same way.
def test_duplicate_keys_last_wins_in_guard_and_handler() -> None:
    assert _refused(_raw("PUT", "/api/v1/connectors/c1/config", _C, b'{"sync": {}, "auth": {}, "auth": {"oauthConfigId": "other"}}'))


def test_a_unicode_escaped_key_is_decoded_before_the_guard() -> None:
    assert _refused(_raw("PUT", "/api/v1/connectors/c1/config", _C, b'{"\\u0061uth": {"oauthConfigId": "other"}}'))


def test_a_text_plain_content_type_is_still_guarded() -> None:
    assert _refused(_raw("PUT", "/api/v1/connectors/c1/config", _C, b'{"auth": {"oauthConfigId": "other"}}', "text/plain"))


def test_snake_and_case_variants_of_the_link_key_are_refused() -> None:
    for key in ("oauth_config_id", "OAuthConfigId"):
        assert _refused(_raw("PUT", "/api/v1/connectors/c1/config", _C, ('{"%s": "other"}' % key).encode()))


def test_ids_in_the_query_or_headers_are_not_read() -> None:
    r = _http_client(_C).request(
        "PUT", "/api/v1/connectors/c1/config", json={"sync": {}}, params={"oauthConfigId": "other"},
        headers={"x-test-user": "PAT or OAuth access token", "x-oauth-config-id": "other"},
    )
    assert not _refused(r)


def test_patch_on_config_is_not_routed() -> None:
    r = _http_client(_C).request("PATCH", "/api/v1/connectors/c1/config", json={"auth": {"oauthConfigId": "other"}}, headers={"x-test-user": "PAT or OAuth access token"})
    assert r.status_code == 405


@pytest.mark.parametrize("body", [
    {"connectorType": "JIRA", "instanceName": "j", "config": {"auth": {"x": None}}},
    {"connectorType": "JIRA", "instanceName": "j", "config": "s"},
    {"connectorType": "JIRA", "instanceName": "j", "auth": {"oauthConfigId": "other"}},
    {"connectorType": "JIRA", "instanceName": "j", "baseUrl": "https://evil.example.invalid"},
])
def test_connector_create_shape_variants_are_refused(body) -> None:
    assert _refused(_raw("POST", "/api/v1/connectors/", _C, json.dumps(body).encode()))


@pytest.mark.parametrize("body", [
    {"auth": {"apiToken": "t"}},
    {"auth": {"x": None}},
])
def test_test_connection_with_credentials_is_refused(body) -> None:
    assert _refused(_raw("POST", "/api/v1/connectors/registry/JIRA/test-connection", _C, json.dumps(body).encode()))


@pytest.mark.parametrize("body", [
    {**_TOOLSET, "authConfig": {"x": None}},
    {**_TOOLSET, "oauthInstanceName": "x"},
    {**_TOOLSET, "scope": "personal", "authConfig": {"clientId": "x"}},
])
def test_toolset_create_variants_are_refused(body) -> None:
    assert _refused(_raw("POST", "/api/v1/toolsets/instances", _T, json.dumps(body).encode()))


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/oauth/JIRA"), ("PUT", "/api/v1/oauth/JIRA/cfg1"), ("DELETE", "/api/v1/oauth/JIRA/cfg1"),
    ("PUT", "/api/v1/toolsets/oauth-configs/slack/cfg1"), ("DELETE", "/api/v1/toolsets/oauth-configs/slack/cfg1"),
])
def test_oauth_app_routes_are_refused_with_no_body_at_all(method, path) -> None:
    module = _T if "toolsets" in path else _C
    assert _refused(_http_client(module).request(method, path, headers={"x-test-user": "PAT or OAuth access token"}))
