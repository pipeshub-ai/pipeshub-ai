"""Strict OpenAPI audit of GET /api/v1/org/exists.

Public route with no validator: the controller only counts org documents, so a
token, a query string or a body changes nothing.
"""

from __future__ import annotations

import pytest
from helper.clients.org_client import OrgClient
from org_audit_support import EXISTS_ROUTE, INVALID_BEARER
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


def test_exists_is_true_on_a_stack_with_an_org(org_client: OrgClient, org_intact: str) -> None:
    resp = org_client.check_exists()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, EXISTS_ROUTE)
    assert resp.json() == {"exists": True}


def test_exists_ignores_a_bad_token(org_client: OrgClient, org_intact: str) -> None:
    resp = org_client.get("/exists", auth=False, headers=INVALID_BEARER)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, EXISTS_ROUTE)
    assert resp.json() == {"exists": True}


def test_exists_ignores_unknown_query_and_body(org_client: OrgClient, org_intact: str) -> None:
    with outside_request_contract("no validator: proves unknown query and body fields are ignored"):
        resp = org_client.get("/exists", auth=False, params={"orgId": "x"}, json={"exists": False})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, EXISTS_ROUTE)
    assert resp.json() == {"exists": True}
