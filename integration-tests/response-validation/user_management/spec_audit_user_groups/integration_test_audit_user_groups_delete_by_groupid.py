"""Strict OpenAPI audit of DELETE /api/v1/userGroups/{groupId} (a soft delete)."""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.user_groups_client import UserGroupsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    ADMIN_REQUIRED,
    GROUP_ROUTE,
    INVALID_BEARER,
    MISSING_ID,
    ScopedCaller,
    error_of,
    request_as,
    system_group,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit


def _not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "User group not found"


def test_delete_returns_the_soft_deleted_group_and_a_second_delete_is_not_found(
    user_groups_client: UserGroupsClient, group: dict[str, Any], pipeshub_client: PipeshubClient
) -> None:
    resp = user_groups_client.delete_group(group["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    body = resp.json()
    assert body["_id"] == group["_id"] and body["isDeleted"] is True
    assert body["deletedBy"] == str(pipeshub_client.acting_user_id)
    _not_found(user_groups_client.delete_group(group["_id"]))


def test_body_is_ignored(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    with outside_request_contract("DELETE documents no body; one sent anyway is dropped"):
        resp = user_groups_client.delete(f"/{group['_id']}", json={"hard": True})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert resp.json()["isDeleted"] is True


@pytest.mark.parametrize("group_type", ["everyone", "standard"])
def test_system_groups_cannot_be_deleted(user_groups_client: UserGroupsClient, group_type: str) -> None:
    target = system_group(user_groups_client, group_type)
    resp = user_groups_client.delete_group(target["_id"])
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "Only custom groups can be deleted"


def test_group_that_does_not_exist_is_not_found(user_groups_client: UserGroupsClient) -> None:
    _not_found(user_groups_client.delete_group(MISSING_ID))


def test_malformed_group_id_fails_validation(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.delete_group("not-an-object-id")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert validation_fields(resp) == {"params.groupId"}


def test_non_admin_is_forbidden(group: dict[str, Any], second_user: SecondUser) -> None:
    resp = request_as(second_user, "DELETE", f"/{group['_id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == ADMIN_REQUIRED


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.delete(f"/{MISSING_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)


def test_oauth_token_without_usergroup_write_is_forbidden(group_read_scope: ScopedCaller, group: dict[str, Any]) -> None:
    resp = group_read_scope("DELETE", f"/{group['_id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:write"
