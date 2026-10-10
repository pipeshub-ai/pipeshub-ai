"""Strict OpenAPI audit of DELETE /api/v1/org/logo.

The logo record is cleared, not removed, so a second delete still succeeds; only
an org that never had a logo record gets the 404.
"""

from __future__ import annotations

import pytest
from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser
from org_audit_support import (
    INVALID_BEARER,
    LOGO_ROUTE,
    SAFE_SVG,
    ScopedCaller,
    error_of,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


@pytest.fixture
def svg_logo(org_client: OrgClient, logo_restored: str) -> str:
    resp = org_client.upload_logo(SAFE_SVG, "logo.svg", "image/svg+xml")
    assert resp.status_code == 201, resp.text[:300]
    return logo_restored


def test_delete_clears_the_logo_and_returns_the_record(org_client: OrgClient, svg_logo: str) -> None:
    resp = org_client.remove_logo()
    assert resp.status_code == 200, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    body = resp.json()
    assert body["logo"] is None and body["mimeType"] is None
    assert body["orgId"] == svg_logo


def test_second_delete_still_succeeds(org_client: OrgClient, svg_logo: str) -> None:
    assert org_client.remove_logo().status_code == 200
    with outside_request_contract("no validator: proves a stray query and body are ignored"):
        resp = org_client.delete("/logo", params={"hard": "true"}, json={"logo": "x"})
        assert resp.status_code == 200, resp.text[:300]
        assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert resp.json()["logo"] is None


def test_org_without_a_logo_record_is_not_found(org_client: OrgClient, no_logo_record: str) -> None:
    resp = org_client.remove_logo()
    assert resp.status_code == 404, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert error_of(resp)["message"] == "Organisation logo not found"


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(org_client: OrgClient, headers: dict[str, str]) -> None:
    resp = org_client.delete("/logo", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_member_is_forbidden(second_user: SecondUser, svg_logo: str) -> None:
    resp = request_as(second_user, "DELETE", "/logo")
    assert resp.status_code == 403, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_oauth_token_without_org_write_is_forbidden(narrow_scope: ScopedCaller, svg_logo: str) -> None:
    resp = narrow_scope("DELETE", "/logo")
    assert resp.status_code == 403, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:write"
