"""Strict OpenAPI audit of POST /api/v1/userGroups.

Only `custom` groups can be created: the validator accepts any non-empty
`type`, and the controller refuses every other value with HTTP_BAD_REQUEST.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.user_groups_client import UserGroupsClient
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    GROUP_EXISTS,
    GROUPS_ROUTE,
    INVALID_BEARER,
    RESERVED_ON_CREATE,
    ScopedCaller,
    error_of,
    request_as,
    unique_group_name,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit


def test_custom_group_is_created_empty(make_group: Any, user_groups_client: UserGroupsClient) -> None:
    name = unique_group_name("create")
    group = make_group(name=f"  {name}  ")
    assert group["name"] == name
    assert group["type"] == "custom"
    assert group["users"] == [] and group["isDeleted"] is False
    assert group["slug"].startswith("usergroup-")


def test_unknown_fields_are_dropped(make_group: Any, second_user: SecondUser) -> None:
    with outside_request_contract("proves fields outside the schema (users, isDeleted) are dropped"):
        group = make_group(users=[second_user.user_id], isDeleted=True)
    assert group["users"] == [] and group["isDeleted"] is False


def test_reserved_name_check_is_case_sensitive(make_group: Any) -> None:
    assert make_group(name="Standard")["name"] == "Standard"


def test_duplicate_name_is_refused(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    resp = user_groups_client.post("/", json={"name": group["name"], "type": "custom"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp) | {"requestId": None} == {"code": "HTTP_BAD_REQUEST", "message": GROUP_EXISTS, "requestId": None}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param({"type": "everyone"}, RESERVED_ON_CREATE, id="type-everyone"),
        pytest.param({"type": "standard"}, RESERVED_ON_CREATE, id="type-standard"),
        pytest.param({"type": "admin"}, RESERVED_ON_CREATE, id="type-admin"),
        pytest.param({"type": "department"}, "type(Type of the Group) unknown", id="type-unknown"),
        pytest.param({"type": "custom", "name": "admin"}, RESERVED_ON_CREATE, id="name-admin"),
        pytest.param({"type": "custom", "name": "everyone"}, RESERVED_ON_CREATE, id="name-everyone"),
    ],
)
def test_reserved_or_unknown_type_or_name_is_refused(
    user_groups_client: UserGroupsClient, body: dict[str, str], message: str
) -> None:
    resp = user_groups_client.post("/", json={"name": unique_group_name(), **body})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert error_of(resp)["message"] == message
    assert_spec_forbids_request(resp, GROUPS_ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({"type": "custom"}, {"body.name"}, id="no-name"),
        pytest.param({"name": "x"}, {"body.type"}, id="no-type"),
        pytest.param({}, {"body.name", "body.type"}, id="empty-body"),
        pytest.param({"name": "", "type": "custom"}, {"body.name"}, id="empty-name"),
        pytest.param({"name": "   ", "type": "custom"}, {"body.name"}, id="blank-name"),
        pytest.param({"name": 5, "type": "custom"}, {"body.name"}, id="name-not-a-string"),
        pytest.param({"name": "x", "type": ""}, {"body.type"}, id="empty-type"),
    ],
)
def test_invalid_body_fails_validation(
    user_groups_client: UserGroupsClient, body: dict[str, Any], fields: set[str]
) -> None:
    resp = user_groups_client.post("/", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert validation_fields(resp) == fields


def test_missing_body_fails_validation(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.post("/")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert validation_fields(resp) == {"body.name", "body.type"}


def test_non_admin_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "", json={"name": unique_group_name(), "type": "custom"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_FORBIDDEN"


def test_body_is_validated_before_the_admin_check(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "", json={})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert validation_fields(resp) == {"body.name", "body.type"}


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.post("/", auth=False, headers=headers, json={"name": "x", "type": "custom"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)


def test_oauth_token_without_usergroup_write_is_forbidden(group_read_scope: ScopedCaller) -> None:
    resp = group_read_scope("POST", "", json={"name": unique_group_name(), "type": "custom"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:write"
