"""Strict OpenAPI audit of PUT /api/v1/userGroups/{groupId}.

The admin check runs before the validator, so a non-admin gets 403 whatever
the request holds.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.user_groups_client import UserGroupsClient
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    ADMIN_REQUIRED,
    GROUP_EXISTS,
    GROUP_ROUTE,
    INVALID_BEARER,
    MISSING_ID,
    RESERVED_ON_RENAME,
    ScopedCaller,
    error_of,
    request_as,
    system_group,
    unique_group_name,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit


def _renamed(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    return resp.json()


def test_rename_trims_the_name(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    name = unique_group_name("renamed")
    got = _renamed(user_groups_client.put(f"/{group['_id']}", json={"name": f"  {name} "}))
    assert got["name"] == name and got["_id"] == group["_id"]


def test_same_name_is_accepted(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    assert _renamed(user_groups_client.put(f"/{group['_id']}", json={"name": group["name"]}))["name"] == group["name"]


def test_unknown_fields_are_dropped(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    with outside_request_contract("proves fields other than name (type, users) are dropped"):
        got = _renamed(user_groups_client.put(f"/{group['_id']}", json={"name": group["name"], "type": "standard", "users": [MISSING_ID]}))
    assert got["type"] == "custom" and got["users"] == []


@pytest.mark.parametrize("name", ["admin", "everyone", "standard"])
def test_reserved_name_is_refused(user_groups_client: UserGroupsClient, group: dict[str, Any], name: str) -> None:
    resp = user_groups_client.put(f"/{group['_id']}", json={"name": name})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == RESERVED_ON_RENAME
    assert_spec_forbids_request(resp, GROUP_ROUTE)


def test_reserved_name_equal_to_the_current_name_is_accepted(user_groups_client: UserGroupsClient) -> None:
    standard = system_group(user_groups_client, "standard")
    with outside_request_contract("the reserved-name check is skipped when the name does not change"):
        got = _renamed(user_groups_client.put(f"/{standard['_id']}", json={"name": "standard"}))
    assert got["name"] == "standard" and got["type"] == "standard"


def test_name_of_another_group_is_refused(
    user_groups_client: UserGroupsClient, make_group: Any, group: dict[str, Any]
) -> None:
    other = make_group()
    resp = user_groups_client.put(f"/{group['_id']}", json={"name": other["name"]})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == GROUP_EXISTS


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-name"),
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"name": "   "}, id="blank-name"),
        pytest.param({"name": 7}, id="name-not-a-string"),
    ],
)
def test_invalid_body_fails_validation(user_groups_client: UserGroupsClient, group: dict[str, Any], body: Any) -> None:
    resp = user_groups_client.put(f"/{group['_id']}", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert validation_fields(resp) == {"body.name"}


def test_missing_body_fails_validation(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    resp = user_groups_client.put(f"/{group['_id']}")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert validation_fields(resp) == {"body.name"}


def test_everyone_group_cannot_be_renamed(user_groups_client: UserGroupsClient) -> None:
    everyone = system_group(user_groups_client, "everyone")
    resp = user_groups_client.put(f"/{everyone['_id']}", json={"name": unique_group_name()})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "Not Allowed"


@pytest.mark.parametrize("deleted", [False, True], ids=["missing", "soft-deleted"])
def test_group_that_does_not_exist_is_not_found(
    user_groups_client: UserGroupsClient, group: dict[str, Any], deleted: bool
) -> None:
    group_id = group["_id"] if deleted else MISSING_ID
    if deleted:
        assert user_groups_client.delete_group(group_id).status_code == 200
    resp = user_groups_client.put(f"/{group_id}", json={"name": unique_group_name()})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "User group not found"


def test_malformed_group_id_fails_validation(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.put("/not-an-object-id", json={"name": "x"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert validation_fields(resp) == {"params.groupId"}


def test_non_admin_is_forbidden_before_validation(group: dict[str, Any], second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", f"/{group['_id']}", json={})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == ADMIN_REQUIRED


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.put(f"/{MISSING_ID}", auth=False, headers=headers, json={"name": "x"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)


def test_oauth_token_without_usergroup_write_is_forbidden(group_read_scope: ScopedCaller, group: dict[str, Any]) -> None:
    resp = group_read_scope("PUT", f"/{group['_id']}", json={"name": unique_group_name()})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:write"
