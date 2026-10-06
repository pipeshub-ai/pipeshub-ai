"""Every /api/v1/org operation answers 500 to a JSON body that does not parse.

The JSON body parser runs before the router, so the failure comes before
authentication and is reported as an internal error. No token is sent, so even
DELETE /org cannot reach its handler.
"""

from __future__ import annotations

import pytest
from helper.clients.org_client import OrgClient
from org_audit_support import (
    EXISTS_ROUTE,
    HEALTH_ROUTE,
    JSON_HEADERS,
    LOGO_ROUTE,
    MALFORMED_JSON_BODY,
    ONBOARDING_ROUTE,
    ORG_ROUTE,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


_OPERATIONS = [
    ("GET", "/exists", EXISTS_ROUTE),
    ("GET", "/health", HEALTH_ROUTE),
    ("POST", "/", ORG_ROUTE),
    ("GET", "/", ORG_ROUTE),
    ("PUT", "/", ORG_ROUTE),
    ("DELETE", "/", ORG_ROUTE),
    ("PUT", "/logo", LOGO_ROUTE),
    ("GET", "/logo", LOGO_ROUTE),
    ("DELETE", "/logo", LOGO_ROUTE),
    ("GET", "/onboarding-status", ONBOARDING_ROUTE),
    ("PUT", "/onboarding-status", ONBOARDING_ROUTE),
]


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [pytest.param(*op, id=f"{op[0]} {op[2]}") for op in _OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error(
    org_client: OrgClient, org_intact: str, method: str, sub_path: str, route: str
) -> None:
    resp = getattr(org_client, method.lower())(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
