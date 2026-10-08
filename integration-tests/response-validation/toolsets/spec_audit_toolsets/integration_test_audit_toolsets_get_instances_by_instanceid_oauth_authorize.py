"""Strict OpenAPI audit of GET /api/v1/toolsets/instances/:instanceId/oauth/authorize.

Node has no validator here and forwards `base_url` only; Python builds the provider's
authorization URL and stores a pending flow under the caller's key (removed with the
instance). Nothing is sent to the provider.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    INSTANCE_NOT_FOUND,
    MISSING_INSTANCE_ID,
    OAUTH_CLIENT_AUTH,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_bad_request,
    assert_not_found,
    decode_state,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/oauth/authorize"
CALLBACK_BASE_URL = "https://spec-audit.invalid"


def _authorize_path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth/authorize"


def _assert_authorization(body: JsonObject, instance_id: str) -> JsonObject:
    assert set(body) == {"success", "authorizationUrl", "state"}, body
    assert body["success"] is True
    query = parse_qs(urlparse(body["authorizationUrl"]).query)
    assert query["state"] == [body["state"]]
    assert query["client_id"] == [OAUTH_CLIENT_AUTH["clientId"]]
    # The redirect URI is fixed when the OAuth config is stored; base_url never changes it.
    assert query["redirect_uri"][0].startswith(f"{CALLBACK_BASE_URL}/")
    state = decode_state(body["state"])
    assert state["instance_id"] == instance_id
    assert "is_agent" not in state
    return state


def test_oauth_instance_returns_authorization_url(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True, baseUrl=CALLBACK_BASE_URL)

    resp = toolsets_client.get(_authorize_path(seeded["_id"]), params={"base_url": "https://ignored.invalid"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    state = _assert_authorization(resp.json(), seeded["_id"])
    assert state["user_id"] == seeded["createdBy"]


def test_member_gets_an_authorization_url_for_their_own_flow(
    second_user: SecondUser, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True, baseUrl=CALLBACK_BASE_URL)

    resp = request_as(second_user, "GET", _authorize_path(seeded["_id"]))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    state = _assert_authorization(resp.json(), seeded["_id"])
    assert state["user_id"] != seeded["createdBy"]


def test_authorize_ignores_a_repeated_base_url_and_unknown_parameters(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True, baseUrl=CALLBACK_BASE_URL)

    with outside_request_contract("no validator: a repeated base_url is joined, unknown parameters are dropped"):
        resp = toolsets_client.get(
            _authorize_path(seeded["_id"]),
            params=[("base_url", "https://a.invalid"), ("base_url", "https://b.invalid"), ("specAuditUnknown", "1")],
        )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    _assert_authorization(resp.json(), seeded["_id"])


def test_api_token_instance_is_refused(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.get(_authorize_path(seeded["_id"]))
    assert_bad_request(
        resp, f"OAuth configuration error: Instance '{seeded['_id']}' uses API_TOKEN authentication, not OAuth."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_oauth_instance_without_an_oauth_config_is_refused(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # Created as OAUTH without client credentials, so no OAuth config was linked.
    seeded = seed_toolset_instance(authType="OAUTH")

    resp = toolsets_client.get(_authorize_path(seeded["_id"]))
    assert_bad_request(
        resp,
        f"OAuth configuration error: Instance '{seeded['_id']}' has no OAuth configuration linked. "
        "Please ask an administrator to update the instance with OAuth credentials.",
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(MISSING_INSTANCE_ID))
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_refused_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authorize_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
