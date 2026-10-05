"""Strict OpenAPI audit of PATCH /api/v1/configurationManager/metricsCollection/pushInterval.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod -> controller.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import MetricsCollectionConfig, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/metricsCollection/pushInterval"
PATH = "/metricsCollection/pushInterval"

# Fails zod, so a gate that wrongly let a caller through still could not write.
UNWRITABLE_BODY: dict[str, Any] = {}


def test_set_push_interval_stores_the_value_as_a_string(
    config_client: ConfigClient, metrics_collection_snapshot: MetricsCollectionConfig
) -> None:
    if "pushIntervalMs" not in metrics_collection_snapshot:
        pytest.skip("no pushIntervalMs stored: writing one could not be undone (no route removes the key)")
    new_interval = int(metrics_collection_snapshot["pushIntervalMs"]) + 1000

    resp = config_client.patch(PATH, json={"pushIntervalMs": new_interval})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Metrics collection push interval set successfully"}

    stored = config_client.get("/metricsCollection")
    assert stored.status_code == 200, stored.text[:500]
    # The validator turns the number into a string before the controller stores it.
    assert stored.json()["pushIntervalMs"] == str(new_interval)


def test_set_push_interval_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.patch(PATH, auth=False, json=UNWRITABLE_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_push_interval_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PATCH", PATH, json=UNWRITABLE_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # The spec names the field pushInterval; the validator requires pushIntervalMs.
        {"pushInterval": 60},
        {"pushIntervalMs": "60000"},
    ],
    ids=["spec_field_name_pushInterval", "interval_not_a_number"],
)
def test_set_push_interval_invalid_body_is_rejected(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.patch(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
