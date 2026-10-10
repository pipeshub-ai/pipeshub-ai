"""Both /api/v1/health routes answer 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the "always 200" health checks fail on a body they never read.
"""

from __future__ import annotations

import pytest
from health_audit_support import (
    HEALTH_ROOT_ROUTE,
    HEALTH_SERVICES_ROUTE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    HealthClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("sub_path", "route"),
    [("", HEALTH_ROOT_ROUTE), ("/services", HEALTH_SERVICES_ROUTE)],
    ids=["infrastructure", "services"],
)
def test_malformed_json_body_is_an_internal_error(
    health_client: HealthClient, sub_path: str, route: str
) -> None:
    resp = health_client.get(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
