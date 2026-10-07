"""Strict OpenAPI audit of DELETE /api/v1/toolsets/instances/:instanceId."""

from __future__ import annotations

import uuid

import pytest
from strict_openapi import assert_strict_openapi_exchange
from toolsets_audit_support import (
    AGENTS_BASE,
    API_TOKEN_AUTH,
    INSTANCE_NOT_FOUND,
    MISSING_INSTANCE_ID,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_path,
    assert_forbidden,
    assert_not_found,
    error_of,
    request_as,
    toolset_store_lock,
)

from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances/:instanceId"
DELETED = "Toolset instance deleted successfully."


def test_admin_deletes_instance_then_it_is_gone(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    with toolset_store_lock():
        resp = toolsets_client.delete_instance(instance_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": DELETED,
        "instanceId": instance_id,
        "deletedCredentialsCount": 0,
    }

    with toolset_store_lock():
        again = toolsets_client.delete_instance(instance_id)
    assert_not_found(again, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_also_removes_every_users_and_agents_saved_credentials(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()
    body = {"auth": dict(API_TOKEN_AUTH)}
    for saved in (
        toolsets_client.post(f"/instances/{instance_id}/authenticate", json=body),
        request_as(second_user, "POST", f"/instances/{instance_id}/authenticate", json=body),
        toolsets_client.post(agent_path(agent_key, instance_id, "/authenticate"), json=body),
    ):
        assert saved.status_code == 200, saved.text[:500]

    with toolset_store_lock():
        resp = toolsets_client.delete_instance(instance_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # The count covers every stored record of the instance, an agent's included.
    assert resp.json()["deletedCredentialsCount"] == 3
    assert resp.json()["message"] == f"{DELETED} 3 user credential(s) were also deleted."


def test_delete_is_refused_while_an_agent_uses_the_instance(
    toolsets_client: ToolsetsClient,
    pipeshub_client: PipeshubClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance()
    agent_name = f"spec-audit-toolsets-{uuid.uuid4().hex[:8]}"
    created = pipeshub_client.request(
        "POST",
        f"{AGENTS_BASE}/create",
        json={
            "name": agent_name,
            "isServiceAccount": False,
            "toolsets": [
                {
                    "name": TOOLSET_TYPE,
                    "instanceId": seeded["_id"],
                    "instanceName": seeded["instanceName"],
                    "tools": [],
                }
            ],
        },
    )
    assert created.status_code in (200, 201), created.text[:500]
    agent_key = created.json()["agent"]["_key"]
    try:
        with toolset_store_lock():
            resp = toolsets_client.delete_instance(seeded["_id"])
        assert resp.status_code == 409, resp.text[:500]
        assert error_of(resp)["message"] == (
            f"Cannot delete toolset '{seeded['instanceName']}': currently in use by agent "
            f"'{agent_name}'. Remove it from the agent first."
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        deleted = pipeshub_client.request("DELETE", f"{AGENTS_BASE}/{agent_key}")
        assert deleted.status_code == 200, deleted.text[:500]


def test_delete_unknown_instance_is_not_found(toolsets_client: ToolsetsClient) -> None:
    with toolset_store_lock():
        resp = toolsets_client.delete_instance(MISSING_INSTANCE_ID)
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete_instance(MISSING_INSTANCE_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_unsafe_instance_id_is_refused_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.delete_instance(UNSAFE_PATH_ID, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_delete_an_existing_instance(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]

    resp = request_as(second_user, "DELETE", f"/instances/{instance_id}")
    assert_forbidden(resp, "Only administrators can delete toolset instances.")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert toolsets_client.get_instance(instance_id).status_code == 200
