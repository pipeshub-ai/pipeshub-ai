"""Strict OpenAPI audit of GET /api/v1/toolsets/agents/:agentKey/instances/:instanceId/oauth/authorize."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from toolsets_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    OAUTH_CLIENT_AUTH,
    UNSAFE_PATH_ID,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_path,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey/instances/:instanceId/oauth/authorize"
CALLBACK_BASE_URL = "https://spec-audit.invalid"


def _authorize_path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/oauth/authorize")


def test_service_account_agent_gets_authorization_url(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    # The redirect URI is fixed when the OAuth config is stored; the authorize call's own
    # base_url is only a fallback for a config without one.
    seeded = seed_toolset_instance(oauth=True, baseUrl=CALLBACK_BASE_URL)
    agent_key = seed_agent()

    # Only builds the URL and stores a pending session that instance teardown removes.
    resp = toolsets_client.get(
        _authorize_path(agent_key, seeded["_id"]), params={"base_url": "https://ignored.invalid"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    query = parse_qs(urlparse(body["authorizationUrl"]).query)
    assert query["state"] == [body["state"]]
    assert query["client_id"] == [OAUTH_CLIENT_AUTH["clientId"]]
    assert query["redirect_uri"][0].startswith(f"{CALLBACK_BASE_URL}/")


def test_regular_agent_is_refused(toolsets_client: ToolsetsClient, seed_agent: SeedAgent) -> None:
    agent_key = seed_agent(is_service_account=False)

    # The service-account check runs before the instance is looked up.
    resp = toolsets_client.get(_authorize_path(agent_key, MISSING_INSTANCE_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_agent_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unsafe_agent_key_is_refused_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(UNSAFE_PATH_ID, MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authorize_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
