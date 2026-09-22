"""MCP server routes must ask for the same OAuth scope on both hops.

Node checks a scope, then forwards the same OAuth token to these Python routes,
which check again. If the two disagree, an app granted what Node asks for is
still refused here, so every MCP route answers 403.
"""

import re
from pathlib import Path

from fastapi.routing import APIRoute

# app.edition_config and app.api.routes.search import each other; load it first.
import app.edition_config  # noqa: F401
from app.api.middlewares.auth import AUTH_POLICY_ATTR
from app.api.routes.mcp_servers import router
from app.config.constants.service import OAuthScopes

NODE_ROUTES = (
    Path(__file__).resolve().parents[5]
    / "backend/nodejs/apps/src/modules/mcp_servers/routes/mcp_servers.routes.ts"
)


def _python_path(node_path: str) -> str:
    """``/instances/:instanceId`` -> ``/instances/{instance_id}``."""
    return re.sub(
        r":(\w+)",
        lambda m: "{" + re.sub(r"(?<!^)([A-Z])", r"_\1", m.group(1)).lower() + "}",
        node_path,
    )


def _node_scopes() -> dict[tuple[str, str], set[str]]:
    source = NODE_ROUTES.read_text()
    found = {}
    for method, path, names in re.findall(
        r"router\.(get|post|put|delete|patch)\(\s*'([^']+)',[^;]*?requireScopes\(([^)]*)\)",
        source,
    ):
        scopes = {OAuthScopes[n].value for n in re.findall(r"OAuthScopeNames\.([A-Z_]+)", names)}
        found[(method.upper(), _python_path(path))] = scopes
    return found


def _python_scopes() -> dict[tuple[str, str], set[str]]:
    found = {}
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        scopes = set()
        pending = [route.dependant]
        while pending:
            dependant = pending.pop()
            policy = getattr(dependant.call, AUTH_POLICY_ATTR, None)
            if policy is not None:
                scopes |= {getattr(s, "value", s) for s in policy.oauth_scopes}
            pending.extend(dependant.dependencies)
        for method in route.methods or ():
            found[(method, route.path.removeprefix(router.prefix))] = scopes
    return found


def test_every_mcp_route_asks_for_the_scope_node_checks():
    node = _node_scopes()
    assert node, f"No routes parsed from {NODE_ROUTES}"
    python = _python_scopes()
    mismatched = {
        key: {"node": node[key], "python": python.get(key)}
        for key in node
        if python.get(key) != node[key]
    }
    assert not mismatched


def test_the_new_mcp_scopes_are_used():
    used = set().union(*_python_scopes().values())
    assert {"mcp:read", "mcp:write", "mcp:delete"} <= used
    assert not {s for s in used if s.startswith("connector:")}
