"""Strict OpenAPI audit of GET /api/v1/userGroups/users/{userId} (any user of the org, for any user)."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.user_groups_client import UserGroupsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    INVALID_BEARER,
    MISSING_ID,
    USER_GROUPS_ROUTE,
    ScopedCaller,
    error_of,
    request_as,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit


def _listed(resp: requests.Response) -> list[dict[str, Any]]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_GROUPS_ROUTE)
    return list(resp.json())


def test_groups_of_a_member(user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser) -> None:
    assert user_groups_client.add_users([second_user.user_id], [group["_id"]]).status_code == 200
    groups = _listed(user_groups_client.get_groups_for_user(second_user.user_id))
    mine = [g for g in groups if g["_id"] == group["_id"]]
    assert mine == [{"_id": group["_id"], "name": group["name"], "type": "custom"}]
    assert {g["type"] for g in groups} >= {"everyone", "custom"}


def test_soft_deleted_groups_are_left_out(
    user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser
) -> None:
    assert user_groups_client.add_users([second_user.user_id], [group["_id"]]).status_code == 200
    assert user_groups_client.delete_group(group["_id"]).status_code == 200
    groups = _listed(user_groups_client.get_groups_for_user(second_user.user_id))
    assert group["_id"] not in {g["_id"] for g in groups}


def test_non_admin_reads_another_users_groups(second_user: SecondUser, pipeshub_client: PipeshubClient) -> None:
    groups = _listed(request_as(second_user, "GET", f"/users/{pipeshub_client.acting_user_id}"))
    assert "everyone" in {g["type"] for g in groups}


def test_user_that_does_not_exist_has_no_groups(user_groups_client: UserGroupsClient) -> None:
    assert _listed(user_groups_client.get_groups_for_user(MISSING_ID)) == []


def test_query_parameters_are_dropped(user_groups_client: UserGroupsClient) -> None:
    with outside_request_contract("the route lists no query parameters; one sent is dropped"):
        assert _listed(user_groups_client.get(f"/users/{MISSING_ID}", params={"type": "custom"})) == []


def test_malformed_user_id_fails_validation(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.get_groups_for_user("not-an-object-id")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_GROUPS_ROUTE)
    assert validation_fields(resp) == {"params.userId"}


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.get(f"/users/{MISSING_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_GROUPS_ROUTE)


def test_oauth_token_without_usergroup_read_is_forbidden(no_group_scope: ScopedCaller) -> None:
    resp = no_group_scope("GET", f"/users/{MISSING_ID}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_GROUPS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:read"
