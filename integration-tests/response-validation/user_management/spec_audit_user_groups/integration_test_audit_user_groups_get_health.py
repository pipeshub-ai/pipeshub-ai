"""Strict OpenAPI audit of GET /api/v1/userGroups/health."""

from __future__ import annotations

import datetime

import pytest
from helper.clients.user_groups_client import UserGroupsClient
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import HEALTH_ROUTE, INVALID_BEARER

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer-is-ignored")],
)
def test_health_needs_no_token(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.get("/health", auth=False, headers=headers)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, HEALTH_ROUTE)
    body = resp.json()
    assert body["status"] == "healthy"
    datetime.datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))


def test_health_with_admin_token(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.get("/health")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, HEALTH_ROUTE)


def test_query_parameters_are_ignored(user_groups_client: UserGroupsClient) -> None:
    with outside_request_contract("the health route reads no query parameters"):
        resp = user_groups_client.get("/health", auth=False, params={"verbose": "true"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, HEALTH_ROUTE)
