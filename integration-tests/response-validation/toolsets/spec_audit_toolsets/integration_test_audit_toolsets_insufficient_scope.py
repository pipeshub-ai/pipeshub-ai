"""An OAuth access token without the route's scope is refused with 403 by the backend.

Node checks no scope on /api/v1/toolsets; the Python route does, and only for OAuth
access tokens (a browser session is never scope-restricted, which is why the member
in the other files gets through). The check is a dependency of the Python handler, so
it runs before the handler reads the request and only where a handler exists: the
legacy /:toolsetId/* routes reach one for the ids listed in RESERVED_SEGMENTS only.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from strict_openapi import assert_strict_openapi_exchange
from toolsets_audit_support import (
    AGENTS_SEGMENT,
    INSTANCES_SEGMENT,
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    MISSING_OAUTH_CONFIG_ID,
    MISSING_TOOLSET_ID,
    NO_BACKEND_ROUTE,
    OAUTH_CONFIGS_SEGMENT,
    TOOLSET_TYPE,
    TOOLSETS_BASE,
    JsonObject,
    assert_not_found,
    error_of,
    instance_body,
)

from helper.pipeshub_client import PipeshubClient

pytestmark = pytest.mark.spec_audit

LEGACY_CONFIG_BODY: JsonObject = {"auth": {"type": "API_TOKEN"}}
CREDENTIALS_BODY: JsonObject = {"auth": {"apiToken": "spec-audit-token"}}
AGENT_INSTANCE = f"/agents/{MISSING_AGENT_KEY}/instances/{MISSING_INSTANCE_ID}"


def _call(
    client: PipeshubClient, headers: dict[str, str], method: str, sub_path: str, **kwargs: Any
) -> requests.Response:
    return requests.request(
        method,
        f"{client.base_url}{TOOLSETS_BASE}{sub_path}",
        headers=headers,
        timeout=client.timeout_seconds,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("method", "sub_path", "route", "body", "scope"),
    [
        pytest.param("GET", "/registry", "/registry", None, "connector:read", id="registry"),
        pytest.param(
            "GET",
            f"/registry/{TOOLSET_TYPE}/schema",
            "/registry/:toolsetType/schema",
            None,
            "connector:read",
            id="registry_schema",
        ),
        pytest.param("GET", "/configured", "/configured", None, "connector:read", id="configured"),
        pytest.param(
            "GET",
            f"/{OAUTH_CONFIGS_SEGMENT}/status",
            "/:toolsetId/status",
            None,
            "connector:read",
            id="legacy_status_as_oauth_config_list",
        ),
        pytest.param(
            "GET",
            f"/{INSTANCES_SEGMENT}/status",
            "/:toolsetId/status",
            None,
            "connector:read",
            id="legacy_status_as_instance_lookup",
        ),
        pytest.param(
            "GET",
            f"/{AGENTS_SEGMENT}/status",
            "/:toolsetId/status",
            None,
            "agent:read",
            id="legacy_status_as_agent_toolsets",
        ),
        pytest.param(
            "GET",
            f"/{OAUTH_CONFIGS_SEGMENT}/config",
            "/:toolsetId/config",
            None,
            "connector:read",
            id="legacy_config_get_as_oauth_config_list",
        ),
        pytest.param(
            "GET",
            f"/{INSTANCES_SEGMENT}/config",
            "/:toolsetId/config",
            None,
            "connector:read",
            id="legacy_config_get_as_instance_lookup",
        ),
        pytest.param(
            "GET",
            f"/{AGENTS_SEGMENT}/config",
            "/:toolsetId/config",
            None,
            "agent:read",
            id="legacy_config_get_as_agent_toolsets",
        ),
        pytest.param(
            "PUT",
            f"/{INSTANCES_SEGMENT}/config",
            "/:toolsetId/config",
            LEGACY_CONFIG_BODY,
            "connector:write",
            id="legacy_config_update_as_instance_update",
        ),
        pytest.param(
            "DELETE",
            f"/{INSTANCES_SEGMENT}/config",
            "/:toolsetId/config",
            None,
            "connector:delete",
            id="legacy_config_delete_as_instance_delete",
        ),
        pytest.param(
            "GET", "/oauth/callback", "/oauth/callback", None, "connector:read", id="oauth_callback"
        ),
        pytest.param("GET", "/my-toolsets", "/my-toolsets", None, "connector:read", id="my_toolsets"),
        pytest.param("GET", "/instances", "/instances", None, "connector:read", id="instances_list"),
        pytest.param(
            "POST", "/instances", "/instances", instance_body(), "connector:write", id="instances_create"
        ),
        pytest.param(
            "GET",
            f"/instances/{MISSING_INSTANCE_ID}",
            "/instances/:instanceId",
            None,
            "connector:read",
            id="instance_get",
        ),
        pytest.param(
            "PUT", f"/instances/{MISSING_INSTANCE_ID}", "/instances/:instanceId", {}, "connector:write",
            id="instance_update",
        ),
        pytest.param(
            "DELETE", f"/instances/{MISSING_INSTANCE_ID}", "/instances/:instanceId", None, "connector:delete",
            id="instance_delete",
        ),
        pytest.param(
            "POST",
            f"/instances/{MISSING_INSTANCE_ID}/authenticate",
            "/instances/:instanceId/authenticate",
            CREDENTIALS_BODY,
            "connector:write",
            id="instance_authenticate",
        ),
        pytest.param(
            "PUT",
            f"/instances/{MISSING_INSTANCE_ID}/credentials",
            "/instances/:instanceId/credentials",
            CREDENTIALS_BODY,
            "connector:write",
            id="instance_credentials_update",
        ),
        pytest.param(
            "DELETE",
            f"/instances/{MISSING_INSTANCE_ID}/credentials",
            "/instances/:instanceId/credentials",
            None,
            "connector:write",
            id="instance_credentials_delete",
        ),
        pytest.param(
            "POST",
            f"/instances/{MISSING_INSTANCE_ID}/reauthenticate",
            "/instances/:instanceId/reauthenticate",
            None,
            "connector:write",
            id="instance_reauthenticate",
        ),
        pytest.param(
            "GET",
            f"/instances/{MISSING_INSTANCE_ID}/oauth/authorize",
            "/instances/:instanceId/oauth/authorize",
            None,
            "connector:write",
            id="instance_authorize",
        ),
        pytest.param(
            "GET",
            f"/instances/{MISSING_INSTANCE_ID}/status",
            "/instances/:instanceId/status",
            None,
            "connector:read",
            id="instance_status",
        ),
        pytest.param(
            "GET", f"/oauth-configs/{TOOLSET_TYPE}", "/oauth-configs/:toolsetType", None, "connector:read",
            id="oauth_configs_list",
        ),
        pytest.param(
            "PUT",
            f"/oauth-configs/{TOOLSET_TYPE}/{MISSING_OAUTH_CONFIG_ID}",
            "/oauth-configs/:toolsetType/:oauthConfigId",
            {},
            "connector:write",
            id="oauth_config_update",
        ),
        pytest.param(
            "DELETE",
            f"/oauth-configs/{TOOLSET_TYPE}/{MISSING_OAUTH_CONFIG_ID}",
            "/oauth-configs/:toolsetType/:oauthConfigId",
            None,
            "connector:delete",
            id="oauth_config_delete",
        ),
        pytest.param("GET", f"/agents/{MISSING_AGENT_KEY}", "/agents/:agentKey", None, "agent:read", id="agent_toolsets"),
        pytest.param(
            "POST",
            f"{AGENT_INSTANCE}/authenticate",
            "/agents/:agentKey/instances/:instanceId/authenticate",
            CREDENTIALS_BODY,
            "agent:write",
            id="agent_authenticate",
        ),
        pytest.param(
            "PUT",
            f"{AGENT_INSTANCE}/credentials",
            "/agents/:agentKey/instances/:instanceId/credentials",
            CREDENTIALS_BODY,
            "agent:write",
            id="agent_credentials_update",
        ),
        pytest.param(
            "DELETE",
            f"{AGENT_INSTANCE}/credentials",
            "/agents/:agentKey/instances/:instanceId/credentials",
            None,
            "agent:write",
            id="agent_credentials_delete",
        ),
        pytest.param(
            "POST",
            f"{AGENT_INSTANCE}/reauthenticate",
            "/agents/:agentKey/instances/:instanceId/reauthenticate",
            None,
            "agent:write",
            id="agent_reauthenticate",
        ),
        pytest.param(
            "GET",
            f"{AGENT_INSTANCE}/oauth/authorize",
            "/agents/:agentKey/instances/:instanceId/oauth/authorize",
            None,
            "agent:write",
            id="agent_authorize",
        ),
    ],
)
def test_oauth_token_without_the_scope_is_forbidden(
    pipeshub_client: PipeshubClient,
    narrow_scope_headers: dict[str, str],
    method: str,
    sub_path: str,
    route: str,
    body: JsonObject | None,
    scope: str,
) -> None:
    kwargs: dict[str, Any] = {} if body is None else {"json": body}
    resp = _call(pipeshub_client, narrow_scope_headers, method, sub_path, **kwargs)

    assert resp.status_code == 403, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert error["message"] == f"Insufficient scope. Required: {scope}", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{TOOLSETS_BASE}{route}")


@pytest.mark.parametrize(
    ("method", "sub_path", "route", "body"),
    [
        pytest.param(
            "POST", "", "", {"name": "spec-audit-legacy", "auth": {"type": "API_TOKEN"}}, id="legacy_create"
        ),
        pytest.param("GET", f"/{MISSING_TOOLSET_ID}/status", "/:toolsetId/status", None, id="legacy_status"),
        pytest.param("GET", f"/{MISSING_TOOLSET_ID}/config", "/:toolsetId/config", None, id="legacy_config_get"),
        pytest.param(
            "POST",
            f"/{MISSING_TOOLSET_ID}/config",
            "/:toolsetId/config",
            LEGACY_CONFIG_BODY,
            id="legacy_config_save",
        ),
        pytest.param(
            "PUT",
            f"/{MISSING_TOOLSET_ID}/config",
            "/:toolsetId/config",
            LEGACY_CONFIG_BODY,
            id="legacy_config_update",
        ),
        pytest.param(
            "DELETE", f"/{MISSING_TOOLSET_ID}/config", "/:toolsetId/config", None, id="legacy_config_delete"
        ),
        pytest.param(
            "POST",
            f"/{MISSING_TOOLSET_ID}/reauthenticate",
            "/:toolsetId/reauthenticate",
            None,
            id="legacy_reauthenticate",
        ),
        pytest.param(
            "GET",
            f"/{MISSING_TOOLSET_ID}/oauth/authorize",
            "/:toolsetId/oauth/authorize",
            None,
            id="legacy_authorize",
        ),
    ],
)
def test_legacy_route_without_a_backend_handler_checks_no_scope(
    pipeshub_client: PipeshubClient,
    narrow_scope_headers: dict[str, str],
    method: str,
    sub_path: str,
    route: str,
    body: JsonObject | None,
) -> None:
    # No Python handler means no scope dependency: the token gets the same routing 404
    # as a fully scoped one.
    kwargs: dict[str, Any] = {} if body is None else {"json": body}
    resp = _call(pipeshub_client, narrow_scope_headers, method, sub_path, **kwargs)

    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, f"{TOOLSETS_BASE}{route}")
