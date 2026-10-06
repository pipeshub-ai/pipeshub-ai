"""Strict OpenAPI audit of GET /api/v1/org/health: public, no validator, always healthy."""

from __future__ import annotations

from datetime import datetime

import pytest
from helper.clients.org_client import OrgClient
from org_audit_support import HEALTH_ROUTE, INVALID_BEARER
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="bad-token")],
)
def test_health_is_healthy_without_auth(org_client: OrgClient, headers: dict[str, str]) -> None:
    resp = org_client.get("/health", auth=False, headers=headers)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, HEALTH_ROUTE)
    body = resp.json()
    assert body["status"] == "healthy"
    datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))


def test_health_ignores_unknown_query_and_body(org_client: OrgClient) -> None:
    with outside_request_contract("no validator: proves unknown query and body fields are ignored"):
        resp = org_client.get("/health", auth=False, params={"deep": "true"}, json={"x": 1})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, HEALTH_ROUTE)
    assert resp.json()["status"] == "healthy"
