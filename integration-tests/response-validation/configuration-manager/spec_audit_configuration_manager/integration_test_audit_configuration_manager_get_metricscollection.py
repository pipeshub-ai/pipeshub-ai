"""Strict OpenAPI audit of GET /api/v1/configurationManager/metricsCollection."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MetricsCollectionConfig,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/metricsCollection"


def test_admin_gets_stored_config(config_client: ConfigClient) -> None:
    resp = config_client.get("/metricsCollection")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # The controller returns the decrypted KV object as-is, {} when nothing is stored.
    assert isinstance(resp.json(), dict)


def test_push_interval_written_through_the_api_is_returned_as_string(
    config_client: ConfigClient,
    metrics_collection_snapshot: MetricsCollectionConfig,
) -> None:
    if "pushIntervalMs" not in metrics_collection_snapshot:
        pytest.skip("no pushIntervalMs stored; writing one could not be undone")
    interval = int(metrics_collection_snapshot["pushIntervalMs"])

    # metricsCollectionPushIntervalSchema turns the number into a string before it is stored.
    written = config_client.patch(
        "/metricsCollection/pushInterval", json={"pushIntervalMs": interval}
    )
    assert written.status_code == 200, written.text[:500]

    resp = config_client.get("/metricsCollection")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["pushIntervalMs"] == str(interval)


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get("/metricsCollection", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/metricsCollection")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
