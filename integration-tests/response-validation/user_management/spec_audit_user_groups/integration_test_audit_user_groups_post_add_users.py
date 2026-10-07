"""Strict OpenAPI audit of POST /api/v1/userGroups/add-users.

The admin check runs before the validator. Neither the users nor the groups
are checked to exist, so an id that names no user is stored; the call fails only when no listed id names
an active group of this organization.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.user_groups_client import UserGroupsClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    ADMIN_REQUIRED,
    ADD_USERS_ROUTE,
    INVALID_BEARER,
    MISSING_ID,
    NO_GROUPS_UPDATED,
    ScopedCaller,
    error_of,
    request_as,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit

ROUTE = ADD_USERS_ROUTE
SUCCESS = "Users added to groups successfully"


def _call(client: UserGroupsClient, body: Any) -> requests.Response:
    return client.post("/add-users", json=body)


def _ok(resp: requests.Response) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": SUCCESS}


def _members(client: UserGroupsClient, group_id: str) -> list[str]:
    resp = client.get_group(group_id)
    assert resp.status_code == 200, resp.text[:300]
    return list(resp.json()["users"])


def test_users_are_added_once_to_every_group(
    user_groups_client: UserGroupsClient, make_group: Any, second_user: SecondUser
) -> None:
    first, second = make_group(), make_group()
    body = {"userIds": [second_user.user_id], "groupIds": [first["_id"], second["_id"]]}
    _ok(_call(user_groups_client, body))
    _ok(_call(user_groups_client, body))
    assert _members(user_groups_client, first["_id"]) == [second_user.user_id]
    assert _members(user_groups_client, second["_id"]) == [second_user.user_id]


def test_user_that_does_not_exist_is_stored(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    _ok(_call(user_groups_client, {"userIds": [MISSING_ID], "groupIds": [group["_id"]]}))
    assert _members(user_groups_client, group["_id"]) == [MISSING_ID]


def test_ids_naming_no_group_are_skipped_when_one_matches(
    user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser
) -> None:
    _ok(_call(user_groups_client, {"userIds": [second_user.user_id], "groupIds": [MISSING_ID, group["_id"]]}))
    assert _members(user_groups_client, group["_id"]) == [second_user.user_id]


def test_group_that_does_not_exist_is_refused(user_groups_client: UserGroupsClient, second_user: SecondUser) -> None:
    resp = _call(user_groups_client, {"userIds": [second_user.user_id], "groupIds": [MISSING_ID]})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert error_of(resp)["message"] == NO_GROUPS_UPDATED


def test_soft_deleted_group_is_refused(
    user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser
) -> None:
    assert user_groups_client.delete_group(group["_id"]).status_code == 200
    resp = _call(user_groups_client, {"userIds": [second_user.user_id], "groupIds": [group["_id"]]})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["message"] == NO_GROUPS_UPDATED


def test_unknown_fields_are_dropped(user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser) -> None:
    with outside_request_contract("proves fields other than userIds and groupIds are dropped"):
        _ok(_call(user_groups_client, {"userIds": [second_user.user_id], "groupIds": [group["_id"]], "role": "admin"}))


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, {"body.userIds", "body.groupIds"}, id="empty-body"),
        pytest.param({"userIds": [], "groupIds": [MISSING_ID]}, {"body.userIds"}, id="no-users"),
        pytest.param({"userIds": [MISSING_ID], "groupIds": []}, {"body.groupIds"}, id="no-groups"),
        pytest.param({"userIds": ["bad"], "groupIds": [MISSING_ID]}, {"body.userIds.0"}, id="malformed-user-id"),
        pytest.param({"userIds": [MISSING_ID], "groupIds": ["bad"]}, {"body.groupIds.0"}, id="malformed-group-id"),
        pytest.param({"userIds": MISSING_ID, "groupIds": [MISSING_ID]}, {"body.userIds"}, id="users-not-an-array"),
    ],
)
def test_invalid_body_fails_validation(user_groups_client: UserGroupsClient, body: Any, fields: set[str]) -> None:
    resp = _call(user_groups_client, body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == fields


def test_missing_body_fails_validation(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.post("/add-users")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert validation_fields(resp) == {"body.userIds", "body.groupIds"}


def test_non_admin_is_forbidden_before_validation(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/add-users", json={})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["message"] == ADMIN_REQUIRED


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.post(
        "/add-users", auth=False, headers=headers, json={"userIds": [MISSING_ID], "groupIds": [MISSING_ID]}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_oauth_token_without_usergroup_write_is_forbidden(group_read_scope: ScopedCaller, group: dict[str, Any]) -> None:
    resp = group_read_scope("POST", "/add-users", json={"userIds": [MISSING_ID], "groupIds": [group["_id"]]})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:write"
