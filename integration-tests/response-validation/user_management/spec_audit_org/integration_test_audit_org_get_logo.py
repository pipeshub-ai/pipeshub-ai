"""Strict OpenAPI audit of GET /api/v1/org/logo: raw image bytes, or 204 without a logo."""

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


def test_logo_is_returned_as_its_bytes(org_client: OrgClient, svg_logo: str) -> None:
    resp = org_client.get_logo()
    assert resp.status_code == 200, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert resp.headers["Content-Type"].startswith("image/svg+xml")
    assert resp.content == SAFE_SVG


def test_member_reads_the_logo(second_user: SecondUser, svg_logo: str) -> None:
    resp = request_as(second_user, "GET", "/logo")
    assert resp.status_code == 200, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert resp.content == SAFE_SVG


def test_unknown_query_is_ignored(org_client: OrgClient, svg_logo: str) -> None:
    with outside_request_contract("no validator: proves an unknown query parameter is ignored"):
        resp = org_client.get("/logo", params={"size": "64"})
        assert resp.status_code == 200, resp.text[:300]
        assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_no_logo_record_is_no_content(org_client: OrgClient, no_logo_record: str) -> None:
    resp = org_client.get_logo()
    assert resp.status_code == 204, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert resp.content == b""


def test_removed_logo_is_no_content(org_client: OrgClient, svg_logo: str) -> None:
    assert org_client.remove_logo().status_code == 200
    resp = org_client.get_logo()
    assert resp.status_code == 204, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert resp.content == b""


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(org_client: OrgClient, headers: dict[str, str]) -> None:
    resp = org_client.get("/logo", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_oauth_token_without_org_read_is_forbidden(narrow_scope: ScopedCaller) -> None:
    resp = narrow_scope("GET", "/logo")
    assert resp.status_code == 403, resp.text[:300]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:read"
