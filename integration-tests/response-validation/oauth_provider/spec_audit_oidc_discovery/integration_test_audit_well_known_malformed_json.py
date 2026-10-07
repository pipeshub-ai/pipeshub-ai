"""Every /.well-known discovery route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so these unauthenticated GET routes fail on a body they never read.
"""

from __future__ import annotations

import pytest
from oidc_discovery_audit_support import (
    AUTHORIZATION_SERVER_ROUTE,
    JSON_HEADERS,
    JWKS_ROUTE,
    MALFORMED_JSON_BODY,
    OPENID_CONFIGURATION_ROUTE,
    PROTECTED_RESOURCE_ROUTE,
    DiscoveryClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTES = [
    OPENID_CONFIGURATION_ROUTE,
    AUTHORIZATION_SERVER_ROUTE,
    PROTECTED_RESOURCE_ROUTE,
    JWKS_ROUTE,
]


@pytest.mark.parametrize("route", ROUTES, ids=[r.rsplit("/", 1)[-1] for r in ROUTES])
def test_malformed_json_body_is_an_internal_error(
    discovery_client: DiscoveryClient, route: str
) -> None:
    resp = discovery_client.fetch(route, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS)

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
