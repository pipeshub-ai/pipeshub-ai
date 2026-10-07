"""Strict OpenAPI audit of PATCH /api/v1/configurationManager/metricsCollection/pushInterval.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod -> controller.
"""

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

ROUTE = "/api/v1/configurationManager/metricsCollection/pushInterval"
PATH = "/metricsCollection/pushInterval"
DEFAULT_PUSH_INTERVAL_MS = 60000

# Fails zod, so a gate that wrongly let a caller through still could not write.
UNWRITABLE_BODY: dict[str, Any] = {}


def _stored(config_client: ConfigClient) -> dict[str, Any]:
    resp = config_client.get("/metricsCollection")
    assert resp.status_code == 200, resp.text[:500]
    return resp.json()


def test_set_push_interval_stores_the_value_as_a_string(
    config_client: ConfigClient, metrics_collection_snapshot: MetricsCollectionConfig
) -> None:
    new_interval = int(metrics_collection_snapshot.get("pushIntervalMs", DEFAULT_PUSH_INTERVAL_MS)) + 1000

    resp = config_client.patch(PATH, json={"pushIntervalMs": new_interval})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "Metrics collection push interval set successfully"}

    # The validator turns the number into a string before the controller stores it.
    assert _stored(config_client)["pushIntervalMs"] == str(new_interval)


def test_a_fractional_interval_is_accepted_and_stored_as_written(
    config_client: ConfigClient, metrics_collection_snapshot: MetricsCollectionConfig
) -> None:
    interval = int(metrics_collection_snapshot.get("pushIntervalMs", DEFAULT_PUSH_INTERVAL_MS)) + 0.5

    resp = config_client.patch(PATH, json={"pushIntervalMs": interval})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client)["pushIntervalMs"] == str(interval)


def test_fields_other_than_the_interval_are_dropped(
    config_client: ConfigClient, metrics_collection_snapshot: MetricsCollectionConfig
) -> None:
    interval = int(metrics_collection_snapshot.get("pushIntervalMs", DEFAULT_PUSH_INTERVAL_MS))

    with outside_request_contract("an unknown body field, to show the validator drops it"):
        resp = config_client.patch(PATH, json={"pushIntervalMs": interval, "serverUrl": "https://spec-audit.invalid"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]

    stored = _stored(config_client)
    assert stored.get("serverUrl") == metrics_collection_snapshot.get("serverUrl")
    assert stored["pushIntervalMs"] == str(interval)


def test_set_push_interval_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.patch(PATH, auth=False, json=UNWRITABLE_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_push_interval_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PATCH", PATH, json=UNWRITABLE_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # The field name the spec used to document; the validator only knows pushIntervalMs.
        {"pushInterval": 60},
        {},
        {"pushIntervalMs": "60000"},
        {"pushIntervalMs": None},
    ],
    ids=["legacy_field_name_pushInterval", "missing", "interval_not_a_number", "interval_null"],
)
def test_set_push_interval_invalid_body_is_rejected(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.patch(PATH, json=body)
    assert_validation_error(resp, "body.pushIntervalMs")
    assert_strict_openapi_exchange(resp, ROUTE)
