"""Strict OpenAPI audit of GET /api/v1/health/services."""

from __future__ import annotations

from datetime import datetime

import pytest
from health_audit_support import (
    HEALTH_SERVICES_ROUTE,
    OVERALL_STATUSES,
    PARSING_SERVICE_KEYS,
    PYTHON_SERVICE_KEYS,
    HealthClient,
)
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = HEALTH_SERVICES_ROUTE
assert ROUTE == "/api/v1/health/services"

# "unknown" only appears when the probe block itself throws.
PROBE_STATUSES = ("healthy", "unhealthy", "unknown")


# The router is mounted without auth middleware, so a missing token is not a 401.
@pytest.mark.parametrize("auth", [True, False], ids=["admin_token", "no_token"])
def test_services_health_is_public_and_always_200(
    health_client: HealthClient, auth: bool
) -> None:
    resp = health_client.services(auth=auth)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"status", "timestamp", "services"}, body
    assert body["status"] in OVERALL_STATUSES, body
    datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))

    services = body["services"]
    assert set(PYTHON_SERVICE_KEYS) <= set(services), services
    assert set(services) <= set(PYTHON_SERVICE_KEYS) | set(PARSING_SERVICE_KEYS), services
    # parsing and extraction are added together, behind USE_PARSING_SERVICE.
    assert len(set(services) & set(PARSING_SERVICE_KEYS)) in (0, len(PARSING_SERVICE_KEYS)), services
    assert all(value in PROBE_STATUSES for value in services.values()), services

    # Only query and connector decide the overall status.
    critical_ok = services["query"] == "healthy" and services["connector"] == "healthy"
    assert body["status"] == ("healthy" if critical_ok else "unhealthy"), body


def test_services_health_ignores_query_parameters(health_client: HealthClient) -> None:
    with outside_request_contract("the handler never reads the query string"):
        resp = health_client.services(auth=False, params={"service": "query"})
    assert resp.status_code == 200, resp.text[:500]
    assert set(PYTHON_SERVICE_KEYS) <= set(resp.json()["services"]), resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
