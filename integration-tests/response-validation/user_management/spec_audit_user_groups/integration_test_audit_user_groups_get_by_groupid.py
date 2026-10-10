"""Strict OpenAPI audit of GET /api/v1/userGroups/{groupId}.

Any user of the organization may read a group, and a soft-deleted group is
still returned (with `isDeleted: true`).
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.user_groups_client import UserGroupsClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
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


def _got(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    return resp.json()


def test_admin_reads_the_group_with_its_members(
    user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser
) -> None:
    assert user_groups_client.add_users([second_user.user_id], [group["_id"]]).status_code == 200
    got = _got(user_groups_client.get_group(group["_id"]))
    assert got["_id"] == group["_id"] and got["name"] == group["name"]
    assert got["users"] == [second_user.user_id]


def test_non_admin_reads_it_too(group: dict[str, Any], second_user: SecondUser) -> None:
    assert _got(request_as(second_user, "GET", f"/{group['_id']}"))["_id"] == group["_id"]


def test_system_group_is_readable(user_groups_client: UserGroupsClient) -> None:
    everyone = system_group(user_groups_client, "everyone")
    got = _got(user_groups_client.get_group(everyone["_id"]))
    assert got["type"] == "everyone"


def test_soft_deleted_group_is_still_returned(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    assert user_groups_client.delete_group(group["_id"]).status_code == 200
    got = _got(user_groups_client.get_group(group["_id"]))
    assert got["isDeleted"] is True
    assert isinstance(got["deletedBy"], str)


def test_query_parameters_are_dropped(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    with outside_request_contract("the route lists no query parameters; one sent is dropped"):
        assert _got(user_groups_client.get(f"/{group['_id']}", params={"fields": "name"}))["users"] == []


def test_group_that_does_not_exist_is_not_found(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.get_group(MISSING_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "UserGroup not found"


def test_malformed_group_id_fails_validation(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.get_group("not-an-object-id")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert validation_fields(resp) == {"params.groupId"}


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.get(f"/{MISSING_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)


def test_oauth_token_without_usergroup_read_is_forbidden(no_group_scope: ScopedCaller, group: dict[str, Any]) -> None:
    resp = no_group_scope("GET", f"/{group['_id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:read"
