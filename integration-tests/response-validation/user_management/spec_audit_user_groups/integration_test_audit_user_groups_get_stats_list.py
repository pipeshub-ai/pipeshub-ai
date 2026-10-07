"""Strict OpenAPI audit of GET /api/v1/userGroups/stats/list (any user of the org)."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.clients.user_groups_client import UserGroupsClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    INVALID_BEARER,
    MISSING_ID,
    STATS_ROUTE,
    ScopedCaller,
    error_of,
    request_as,
)

pytestmark = pytest.mark.spec_audit


def _stats(resp: requests.Response) -> dict[str, dict[str, Any]]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, STATS_ROUTE)
    return {row["_id"]: row for row in resp.json()}


def test_one_row_per_group_name(user_groups_client: UserGroupsClient, group: dict[str, Any], second_user: SecondUser) -> None:
    assert user_groups_client.add_users([second_user.user_id, MISSING_ID], [group["_id"]]).status_code == 200
    rows = _stats(user_groups_client.get_group_statistics())
    assert rows[group["name"]] == {"_id": group["name"], "count": 1, "totalUsers": 2, "avgUsers": 2}
    assert "everyone" in rows


def test_soft_deleted_groups_are_left_out(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    assert user_groups_client.delete_group(group["_id"]).status_code == 200
    assert group["name"] not in _stats(user_groups_client.get_group_statistics())


def test_non_admin_reads_the_statistics(group: dict[str, Any], second_user: SecondUser) -> None:
    assert group["name"] in _stats(request_as(second_user, "GET", "/stats/list"))


def test_query_parameters_are_ignored(user_groups_client: UserGroupsClient) -> None:
    with outside_request_contract("the route has no validator and reads no query parameters"):
        assert "everyone" in _stats(user_groups_client.get("/stats/list", params={"type": "custom"}))


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.get("/stats/list", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, STATS_ROUTE)


def test_oauth_token_without_usergroup_read_is_forbidden(no_group_scope: ScopedCaller) -> None:
    resp = no_group_scope("GET", "/stats/list")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, STATS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:read"
