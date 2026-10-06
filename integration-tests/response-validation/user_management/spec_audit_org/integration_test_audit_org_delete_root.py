"""Strict OpenAPI audit of DELETE /api/v1/org.

Refusals only: a successful call soft-deletes the org every suite shares, so the
admin is never the caller here.
"""

from __future__ import annotations

import pytest
from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser
from org_audit_support import INVALID_BEARER, ORG_ROUTE, ScopedCaller, error_of, request_as
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ADMIN_REQUIRED_STATUS = 403


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param(INVALID_BEARER, id="malformed-bearer"),
        # extractToken only accepts the Bearer scheme; anything else reads as no token.
        pytest.param({"Authorization": "Basic c3BlYzphdWRpdA=="}, id="basic-scheme"),
    ],
)
def test_delete_org_without_valid_token_is_unauthorized(
    org_client: OrgClient, org_intact: str, headers: dict[str, str]
) -> None:
    resp = org_client.delete("/", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)


def test_delete_org_as_member_is_forbidden(
    second_user: SecondUser, org_intact: str
) -> None:
    resp = request_as(second_user, "DELETE")
    assert resp.status_code == ADMIN_REQUIRED_STATUS, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)


def test_delete_org_as_member_checks_admin_before_reading_the_request(
    second_user: SecondUser, org_intact: str
) -> None:
    # No validator sits on this route, so a stray body and query must not turn the 403 into a 400.
    with outside_request_contract("no validator: a stray query and body are never read"):
        resp = request_as(
            second_user, "DELETE", params={"force": "true"}, json={"orgId": org_intact}
        )
        assert resp.status_code == ADMIN_REQUIRED_STATUS, resp.text[:500]
        assert_strict_openapi_exchange(resp, ORG_ROUTE)


def test_delete_org_with_token_lacking_org_admin_is_forbidden(
    narrow_scope: ScopedCaller, org_intact: str
) -> None:
    resp = narrow_scope("DELETE")
    assert resp.status_code == ADMIN_REQUIRED_STATUS, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:admin"
