"""Every mounted API route must declare which bearer-token classes it admits.

The auth middleware accepts service tokens for a small set of internal flows; a
route only admits one when its dependency opts in. These tests fail when a route
has no token policy at all, or when the set of routes reachable with a service
token changes without this file being reviewed.
"""

import importlib

import pytest
from fastapi.routing import APIRoute

# The service mains load the edition seams before any router; app.edition_config and
# app.api.routes.search import each other, so importing a router first would fail.
import app.edition_config  # noqa: F401
from app.api.middlewares.auth import AUTH_POLICY_ATTR, AuthPolicy

# Routers mounted by query_main and connectors_main. EE routers are None in CE;
# the EE build must run these tests against its own routers too.
_ROUTERS = {
    "search": ("app.api.routes.search", "router"),
    "chatbot": ("app.api.routes.chatbot", "router"),
    "speech": ("app.api.routes.speech", "router"),
    "agent": ("app.api.routes.agent", "router"),
    "skills": ("app.api.routes.skills", "router"),
    "toolsets": ("app.api.routes.toolsets", "router"),
    "health": ("app.api.routes.health", "router"),
    "ai_models_registry": ("app.api.routes.ai_models_registry", "router"),
    "entity": ("app.api.routes.entity", "router"),
    "mcp_servers": ("app.api.routes.mcp_servers", "router"),
    "kb": ("app.connectors.sources.localKB.api.kb_router", "kb_router"),
    "knowledge_hub": (
        "app.connectors.sources.localKB.api.knowledge_hub_router",
        "knowledge_hub_router",
    ),
    "connectors": ("app.connectors.api.router", "router"),
}

# Every route that admits a service token, with the scopes it admits.
_SERVICE_TOKEN_ROUTES = {
    ("agent", "POST", "/{agent_id}/chat/stream"): {"conversation:create"},
    ("agent", "GET", "/{agent_id}/internal/service-account"): {"conversation:create"},
    ("chatbot", "POST", "/chat/attachments/upload"): {"conversation:create"},
    (
        "connectors",
        "GET",
        "/api/v1/{org_id}/{user_id}/{connector}/record/{record_id}/signedUrl",
    ): {"connector:signedUrl"},
    ("connectors", "GET", "/api/v1/internal/stream/record/{record_id}/"): {"connector:signedUrl"},
    ("connectors", "GET", "/api/v1/index/{org_id}/{connector}/record/{record_id}"): {
        "connector:signedUrl"
    },
    ("connectors", "GET", "/api/v1/internal/records/{record_id}/content"): {"record:content"},
    ("connectors", "GET", "/api/v1/connectors/internal/all-scheduled"): {"fetch:config"},
}


def _mounted_routes() -> list[tuple[str, APIRoute]]:
    routes = []
    for name, (module, attr) in _ROUTERS.items():
        router = getattr(importlib.import_module(module), attr)
        routes.extend((name, route) for route in router.routes if isinstance(route, APIRoute))
    return routes


def _policies(route: APIRoute) -> list[AuthPolicy]:
    found = []
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        policy = getattr(dependant.call, AUTH_POLICY_ATTR, None)
        if policy is not None:
            found.append(policy)
        pending.extend(dependant.dependencies)
    return found


def _methods(route: APIRoute) -> list[str]:
    return sorted(route.methods or ())


_ROUTES = _mounted_routes()


@pytest.mark.parametrize(
    ("router_name", "route"),
    _ROUTES,
    ids=[f"{name}:{'/'.join(_methods(route))}:{route.path}" for name, route in _ROUTES],
)
def test_every_route_declares_a_token_policy(router_name, route):
    assert _policies(route), (
        f"{router_name} {_methods(route)} {route.path} has no require_scopes, "
        "require_service_token or deny_service_tokens dependency"
    )


def test_service_token_routes_match_the_reviewed_list():
    admitted = {}
    for name, route in _ROUTES:
        scopes = set().union(*(policy.service_scopes for policy in _policies(route)))
        if scopes:
            for method in _methods(route):
                admitted[(name, method, route.path)] = scopes
    assert admitted == _SERVICE_TOKEN_ROUTES


def test_internal_service_routes_reject_user_tokens():
    """Routes built for service callers must not also accept session/OAuth tokens."""
    service_only = {
        key
        for key in _SERVICE_TOKEN_ROUTES
        if "/internal/" in key[2] and not key[2].endswith("/service-account")
    }
    for name, route in _ROUTES:
        for method in _methods(route):
            if (name, method, route.path) in service_only:
                assert any(policy.kind == "service" for policy in _policies(route)), route.path
