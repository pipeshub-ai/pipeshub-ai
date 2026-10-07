"""Strict OpenAPI audit of DELETE /api/v1/toolsets/instances/:instanceId/credentials.

Node only authenticates and forwards to Python `remove_toolset_credentials`, which deletes
the caller's own credential key without looking the instance up, so any id answers 200.
"""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_exchange
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId/credentials"
REMOVED: JsonObject = {"status": "success", "message": "Credentials removed successfully."}


def _authenticate(toolsets_client: ToolsetsClient, instance_id: str) -> None:
    saved = toolsets_client.post(
        f"/instances/{instance_id}/authenticate", json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]


def _is_authenticated(toolsets_client: ToolsetsClient, instance_id: str) -> bool:
    status = toolsets_client.get(f"/instances/{instance_id}/status")
    assert status.status_code == 200, status.text[:500]
    return bool(status.json().get("isAuthenticated"))


def test_remove_saved_credentials(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    _authenticate(toolsets_client, instance_id)
    assert _is_authenticated(toolsets_client, instance_id)

    resp = toolsets_client.delete(f"/instances/{instance_id}/credentials")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == REMOVED

    assert not _is_authenticated(toolsets_client, instance_id)


@pytest.mark.parametrize("oauth", [False, True], ids=["never-authenticated", "oauth-instance"])
def test_remove_credentials_that_were_never_saved_still_succeeds(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance, oauth: bool
) -> None:
    instance_id = seed_toolset_instance(oauth=oauth)["_id"]

    resp = toolsets_client.delete(f"/instances/{instance_id}/credentials")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == REMOVED


def test_remove_credentials_of_unknown_instance_still_succeeds(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.delete(f"/instances/{MISSING_INSTANCE_ID}/credentials")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == REMOVED


def test_member_removes_only_their_own_credentials(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    second_user: SecondUser,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    _authenticate(toolsets_client, instance_id)

    resp = request_as(second_user, "DELETE", f"/instances/{instance_id}/credentials")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    assert _is_authenticated(toolsets_client, instance_id)


def test_remove_credentials_without_token_is_unauthorized(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.delete(f"/instances/{MISSING_INSTANCE_ID}/credentials", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_credentials_with_unsafe_instance_id_is_rejected_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.delete(f"/instances/{UNSAFE_PATH_ID}/credentials", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
