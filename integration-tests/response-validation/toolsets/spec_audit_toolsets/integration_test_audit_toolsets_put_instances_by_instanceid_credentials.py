"""Strict OpenAPI audit of PUT /api/v1/toolsets/instances/:instanceId/credentials."""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_INSTANCE_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/credentials"
# Node's zod schema keeps only email/apiToken/username/password inside auth and drops
# every other key, so baseUrl can be set by /authenticate but never by this route.
VALID_BODY: JsonObject = {
    "auth": {"email": "spec-audit-updated@example.com", "apiToken": "spec-audit-token-2"}
}


def _credentials_path(instance_id: str) -> str:
    return f"/instances/{instance_id}/credentials"


def test_update_credentials_replaces_the_callers_saved_credentials(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    # The route only updates: the caller's credentials must already be saved.
    # Deleting the seeded instance on teardown removes them again.
    saved = toolsets_client.post(
        f"/instances/{instance_id}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]

    resp = toolsets_client.put(_credentials_path(instance_id), json=VALID_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_credentials_is_not_found_before_the_caller_authenticated(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    resp = toolsets_client.put(_credentials_path(instance_id), json=VALID_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_credentials_refuses_an_oauth_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    instance_id = seed_toolset_instance(oauth=True)["_id"]
    resp = toolsets_client.put(_credentials_path(instance_id), json=VALID_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_credentials_rejects_a_body_without_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(
        _credentials_path(MISSING_INSTANCE_ID), json={"apiToken": "spec-audit-token-2"}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_credentials_rejects_a_call_without_a_token(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(
        _credentials_path(MISSING_INSTANCE_ID), auth=False, json=VALID_BODY
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
