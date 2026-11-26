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
    "record_access": ("app.api.routes.record_access", "router"),
    "control_plane_drivers": ("app.api.routes.control_plane_drivers", "router"),
    "ai_models_registry": ("app.api.routes.ai_models_registry", "router"),
    "code_graph": ("app.api.routes.code_graph", "router"),
    "graph_view": ("app.api.routes.graph_view", "router"),
    "entity": ("app.api.routes.entity", "router"),
    "knowledge_search": ("app.api.routes.knowledge_search", "router"),
    "entity_tools": ("app.api.routes.entity_tools", "router"),
    "mcp_servers": ("app.api.routes.mcp_servers", "router"),
    "api_toolsets": ("app.api.routes.api_toolsets", "router"),
    "kb": ("app.connectors.sources.localKB.api.kb_router", "kb_router"),
    "knowledge_hub": (
        "app.connectors.sources.localKB.api.knowledge_hub_router",
        "knowledge_hub_router",
    ),
    "connectors": ("app.connectors.api.router", "router"),
    "artifacts": ("app.connectors.api.artifacts_router", "artifacts_router"),
    "parsing": ("app.api.routes.parsing", "router"),
    "extraction": ("app.api.routes.extraction", "router"),
}

# Every route that admits a service token, with the scopes it admits.
_SERVICE_TOKEN_ROUTES = {
    ("agent", "POST", "/{agent_id}/chat/stream"): {"conversation:create"},
    ("agent", "GET", "/{agent_id}/internal/service-account"): {"conversation:create"},
    # The Slack bot's durable admission of a service-account agent (P1b C-5).
    ("agent", "GET", "/{agent_id}/run-definition"): {"conversation:create"},
    # Node's agent-erasure sweeper (H5): an agent's tombstone, before it erases the agent's memories.
    ("agent", "GET", "/{agent_id}/internal/deletion"): {"agent:erase"},
    # WP-51: the usage report and budget notices name agents by key, for the token's org only.
    ("agent", "POST", "/internal/names"): {"agent:names"},
    # Node's control plane (CPD-7): one org's legacy capability facts for its migration and sync.
    ("control_plane_drivers", "GET", "/legacy/capabilities"): {"capabilities:read"},
    ("control_plane_drivers", "GET", "/directory/users/{userId}/teams"): {"directory:read"},
    # Node's classification approver routing (OWD-15): whether a team of the token's org has an admin besides the
    # requester. A count only, org from the token.
    ("control_plane_drivers", "GET", "/directory/teams/{teamId}/owners"): {"directory:read"},
    ("chatbot", "POST", "/chat/attachments/upload"): {"conversation:create"},
    ("chatbot", "POST", "/chat/attachments/permissions"): {"conversation:permissions"},
    ("chatbot", "DELETE", "/chat/attachments/permissions"): {"conversation:permissions"},
    ("chatbot", "POST", "/chat/artifacts/permissions"): {"conversation:permissions"},
    ("chatbot", "DELETE", "/chat/artifacts/permissions"): {"conversation:permissions"},
    # Node's erasure job (E7): a deleted conversation's artifacts, discarded drafts.
    ("chatbot", "POST", "/chat/artifacts/erase"): {"conversation:erase"},
    # ...and the chat attachments its messages carried (QA15-F4).
    ("chatbot", "POST", "/chat/attachments/erase"): {"conversation:erase"},
    # Node's unsent-upload collector (CLEANUP-G5): the token's org's uploads waiting for a message, and marking
    # those a message carries as sent. Org from the token, never the body.
    ("chatbot", "POST", "/chat/attachments/unsent"): {"conversation:erase"},
    ("chatbot", "POST", "/chat/attachments/sent"): {"conversation:erase"},
    # Node's org erasure (CLEANUP-G1): one page of the token's org's uploads, artifacts and
    # skill drafts; the body names no org.
    ("chatbot", "POST", "/chat/org/erase"): {"conversation:erase"},
    # Node's session erasure step `traces` (CLEANUP-OPIK): a conversation's traces in Opik, ids checked against
    # the token's org's conversation and runs.
    ("chatbot", "POST", "/chat/traces/erase"): {"conversation:erase"},
    # "Continue privately" (SS3): a conversation's artifact versions copied into the forker's copy.
    ("chatbot", "POST", "/chat/artifacts/copy"): {"conversation:fork"},
    ("chatbot", "POST", "/chat/records/access"): {"conversation:permissions"},
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
    ("parsing", "POST", "/api/v1/parse"): {"document:parse"},
    ("parsing", "GET", "/api/v1/parse/providers"): {"document:parse"},
    ("extraction", "POST", "/api/v1/extract/classify"): {"document:classify"},
    ("entity", "PATCH", "/api/v1/entity/user/email"): {"entity:user:write"},
    # Node user and org deletion (C-14): a deleted principal's personal toolset and MCP instances.
    ("entity", "POST", "/api/v1/entity/internal/personal-instances/erase"): {"principal:erase"},
    ("entity", "POST", "/api/v1/entity/internal/user-node"): {"entity:user:ensure"},
    ("extraction", "POST", "/api/v1/extract/entities"): {"document:classify"},
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


def test_inventoried_routers_hold_a_flat_list_of_routes():
    for name, (module, attr) in _ROUTERS.items():
        router = getattr(importlib.import_module(module), attr)
        for route in router.routes:
            assert isinstance(route, APIRoute), (
                f"{name} router holds {route!r}; _mounted_routes() only collects APIRoute, "
                "so it and any routes under it would go unchecked"
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
        if key[0] in {"parsing", "extraction"}
        or ("/internal/" in key[2] and not key[2].endswith("/service-account"))
    }
    for name, route in _ROUTES:
        for method in _methods(route):
            if (name, method, route.path) in service_only:
                assert any(policy.kind == "service" for policy in _policies(route)), route.path
