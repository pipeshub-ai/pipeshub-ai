"""Strict OpenAPI audit of PUT /api/v1/configurationManager/metricsCollection/toggle."""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    MetricsCollectionConfig,
    assert_validation_error,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/metricsCollection/toggle"

VALID_BODY: dict[str, Any] = {"enableMetricCollection": True}


def test_toggle_metrics_collection_flips_the_stored_switch(
    config_client: ConfigClient,
    metrics_collection_snapshot: MetricsCollectionConfig,
) -> None:
    # Absent means enabled; the fixture removes the key again when it was absent.
    enabled_before = metrics_collection_snapshot.get("enableMetricCollection", "true") not in (False, "false")

    resp = config_client.put("/metricsCollection/toggle", json={"enableMetricCollection": not enabled_before})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"message": "Metrics collection toggled successfully"}
    assert_strict_openapi_exchange(resp, ROUTE)

    stored = config_client.get("/metricsCollection")
    assert stored.status_code == 200, stored.text[:500]
    # The validator turns the boolean into a string before it is stored.
    expected = {**metrics_collection_snapshot, "enableMetricCollection": str(not enabled_before).lower()}
    assert stored.json() == expected


def test_fields_other_than_the_switch_are_dropped(
    config_client: ConfigClient,
    metrics_collection_snapshot: MetricsCollectionConfig,
) -> None:
    enabled = metrics_collection_snapshot.get("enableMetricCollection", "true") not in (False, "false")

    with outside_request_contract("an unknown body field, to show the validator drops it"):
        resp = config_client.put(
            "/metricsCollection/toggle", json={"enableMetricCollection": enabled, "pushIntervalMs": 1}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]

    stored = config_client.get("/metricsCollection")
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json().get("pushIntervalMs") == metrics_collection_snapshot.get("pushIntervalMs")


def test_toggle_metrics_collection_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.put("/metricsCollection/toggle", auth=False, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_toggle_metrics_collection_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", "/metricsCollection/toggle", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # The store holds the string form, but the route only takes a real boolean.
        pytest.param({"enableMetricCollection": "true"}, id="string-instead-of-boolean"),
        pytest.param({}, id="missing-flag"),
        pytest.param({"enableMetricCollection": None}, id="null-flag"),
    ],
)
def test_toggle_metrics_collection_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.put("/metricsCollection/toggle", json=body)

    assert_validation_error(resp, "body.enableMetricCollection")
    assert_strict_openapi_exchange(resp, ROUTE)
