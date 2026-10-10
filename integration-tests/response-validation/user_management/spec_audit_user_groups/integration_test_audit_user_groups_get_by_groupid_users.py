"""Strict OpenAPI audit of GET /api/v1/userGroups/{groupId}/users (any user of the org)."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.user_groups_client import UserGroupsClient
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    GROUP_USERS_ROUTE,
    INVALID_BEARER,
    MISSING_ID,
    ScopedCaller,
    error_of,
    request_as,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit


def _listed(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_USERS_ROUTE)
    return resp.json()


@pytest.fixture
def member_group(user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser) -> dict[str, Any]:
    resp = user_groups_client.add_users([second_user.user_id, MISSING_ID], [group["_id"]])
    assert resp.status_code == 200, resp.text[:300]
    return group


def test_members_are_listed_and_unknown_ids_left_out(
    user_groups_client: UserGroupsClient, member_group: dict[str, Any], second_user: SecondUser
) -> None:
    body = _listed(user_groups_client.get_users_in_group(member_group["_id"]))
    assert body["users"] == [
        {"_id": second_user.user_id, "fullName": body["users"][0]["fullName"], "email": second_user.email, "profilePicture": None}
    ]
    assert body["pagination"] == {
        "page": 1, "limit": 25, "totalCount": 1, "totalPages": 1, "hasNextPage": False, "hasPrevPage": False,
    }


def test_search_by_email(user_groups_client: UserGroupsClient, member_group: dict[str, Any], second_user: SecondUser) -> None:
    assert len(_listed(user_groups_client.get_users_in_group(member_group["_id"], search=second_user.email))["users"]) == 1
    assert _listed(user_groups_client.get_users_in_group(member_group["_id"], search="no-such-person"))["users"] == []


def test_page_past_the_end_is_empty(user_groups_client: UserGroupsClient, member_group: dict[str, Any]) -> None:
    body = _listed(user_groups_client.get_users_in_group(member_group["_id"], page=2, limit=1))
    assert body["users"] == []
    assert body["pagination"]["hasPrevPage"] is True and body["pagination"]["hasNextPage"] is False


def test_non_admin_lists_members_too(member_group: dict[str, Any], second_user: SecondUser) -> None:
    assert len(_listed(request_as(second_user, "GET", f"/{member_group['_id']}/users"))["users"]) == 1


def test_unknown_query_parameters_are_dropped(user_groups_client: UserGroupsClient, member_group: dict[str, Any]) -> None:
    with outside_request_contract("proves a query parameter the spec does not list is dropped"):
        assert len(_listed(user_groups_client.get_users_in_group(member_group["_id"], role="admin"))["users"]) == 1


def test_fractional_page_is_truncated(user_groups_client: UserGroupsClient, member_group: dict[str, Any]) -> None:
    with outside_request_contract("page is read with parseInt, so 1.5 is tolerated as 1"):
        body = _listed(user_groups_client.get_users_in_group(member_group["_id"], page="1.5"))
    assert body["pagination"]["page"] == 1


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": "0"}, id="page-zero"),
        pytest.param({"limit": "101"}, id="limit-over-100"),
        pytest.param({"limit": "abc"}, id="limit-not-a-number"),
    ],
)
def test_out_of_range_paging_is_an_internal_error(
    user_groups_client: UserGroupsClient, group: dict[str, Any], params: dict[str, str]
) -> None:
    resp = user_groups_client.get_users_in_group(group["_id"], **params)
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_USERS_ROUTE)
    assert error_of(resp)["code"] == "INTERNAL_ERROR"
    assert_spec_forbids_request(resp, GROUP_USERS_ROUTE)


def test_markup_in_search_is_refused(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    resp = user_groups_client.get_users_in_group(group["_id"], search="<b>x</b>")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_USERS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert_spec_forbids_request(resp, GROUP_USERS_ROUTE)


@pytest.mark.parametrize("deleted", [False, True], ids=["missing", "soft-deleted"])
def test_group_that_does_not_exist_is_not_found(
    user_groups_client: UserGroupsClient, group: dict[str, Any], deleted: bool
) -> None:
    group_id = group["_id"] if deleted else MISSING_ID
    if deleted:
        assert user_groups_client.delete_group(group_id).status_code == 200
    resp = user_groups_client.get_users_in_group(group_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_USERS_ROUTE)
    assert error_of(resp)["message"] == "Group not found"


def test_malformed_group_id_fails_validation(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.get_users_in_group("not-an-object-id")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_USERS_ROUTE)
    assert validation_fields(resp) == {"params.groupId"}


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.get(f"/{MISSING_ID}/users", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_USERS_ROUTE)


def test_oauth_token_without_usergroup_read_is_forbidden(no_group_scope: ScopedCaller, group: dict[str, Any]) -> None:
    resp = no_group_scope("GET", f"/{group['_id']}/users")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUP_USERS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:read"
