"""Strict OpenAPI audit of GET /api/v1/health."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
import requests
from health_audit_support import (
    DEPLOYMENT_KEYS,
    ETCD_SERVICE_KEY,
    HEALTH_ROOT_ROUTE,
    INFRA_SERVICE_KEYS,
    OVERALL_STATUSES,
    HealthClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

TOP_LEVEL_KEYS = ("status", "timestamp", "services", "serviceNames", "deployment")
INFRA_STATUSES = ("healthy", "unhealthy")
# graphDb keeps its initial "unknown" when dataStoreType is neither neo4j nor arangodb.
EXTRA_STATUSES = {"graphDb": ("pending", "unknown"), "vectorDb": ("pending",)}


def _assert_health_body(resp: requests.Response) -> dict[str, Any]:
    # A down dependency is reported in the body; the HTTP status stays 200.
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, HEALTH_ROOT_ROUTE)
    body: dict[str, Any] = resp.json()
    assert set(body) == set(TOP_LEVEL_KEYS), body
    assert body["status"] in OVERALL_STATUSES, body
    assert body["timestamp"].endswith("Z"), body
    datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
    return body


@pytest.mark.parametrize(
    "auth, headers",
    [
        pytest.param(True, {}, id="admin-token"),
        pytest.param(False, {}, id="no-token"),
        pytest.param(False, {"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_health_is_public_and_reports_every_infra_service(
    health_client: HealthClient, auth: bool, headers: dict[str, str]
) -> None:
    body = _assert_health_body(health_client.get("", auth=auth, headers=headers))

    services: dict[str, str] = body["services"]
    etcd_keys = {ETCD_SERVICE_KEY} if body["deployment"]["kvStoreType"] == "etcd" else set()
    assert set(services) == set(INFRA_SERVICE_KEYS) | etcd_keys, body
    for key, value in services.items():
        assert value in INFRA_STATUSES + EXTRA_STATUSES.get(key, ()), body

    assert set(body["serviceNames"]) == set(services), body
    assert all(isinstance(name, str) and name for name in body["serviceNames"].values()), body
    assert set(body["deployment"]) == set(DEPLOYMENT_KEYS), body

    any_unhealthy = "unhealthy" in services.values()
    assert body["status"] == ("unhealthy" if any_unhealthy else "healthy"), body


def test_health_ignores_unknown_query_params(health_client: HealthClient) -> None:
    # No validator on the route, so a stray param is not a 400.
    body = _assert_health_body(
        health_client.get("", auth=False, params={"verbose": "bogus"})
    )

    deployment: dict[str, str] = body["deployment"]
    assert (body["services"]["graphDb"] == "pending") == (deployment["graphDbType"] == "pending"), body
    assert (body["services"]["vectorDb"] == "pending") == (deployment["vectorDbType"] == "pending"), body
