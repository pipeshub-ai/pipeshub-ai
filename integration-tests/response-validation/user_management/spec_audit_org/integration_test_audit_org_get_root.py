"""Strict OpenAPI audit of GET /api/v1/org: any member of the org may read it."""

from __future__ import annotations

import pytest
from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser
from org_audit_support import (
    INVALID_BEARER,
    ORG_ROUTE,
    ScopedCaller,
    error_of,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


def test_admin_reads_the_org(org_client: OrgClient, org_intact: str) -> None:
    resp = org_client.get_organization()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    body = resp.json()
    assert body["_id"] == org_intact
    assert body["isDeleted"] is False


def test_member_reads_the_org(second_user: SecondUser, org_intact: str) -> None:
    resp = request_as(second_user, "GET")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert resp.json()["_id"] == org_intact


def test_unknown_query_is_ignored(org_client: OrgClient, org_intact: str) -> None:
    with outside_request_contract("no validator: proves an unknown query parameter is ignored"):
        resp = org_client.get("/", params={"orgId": "000000000000000000000000"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert resp.json()["_id"] == org_intact


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(
    org_client: OrgClient, headers: dict[str, str]
) -> None:
    resp = org_client.get("/", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)


def test_oauth_token_without_org_read_is_forbidden(narrow_scope: ScopedCaller) -> None:
    resp = narrow_scope("GET")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:read"
