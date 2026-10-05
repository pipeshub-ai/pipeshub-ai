"""Strict OpenAPI audit of PATCH /api/v1/configurationManager/metricsCollection/serverUrl.

The URL is where every service pushes its telemetry, and no route can remove the
key once it exists, so the success case only sends back the value already stored.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import MetricsCollectionConfig, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/metricsCollection/serverUrl"
PATH = "/metricsCollection/serverUrl"

# Fails z.string().url(), so a gate that wrongly let a caller through still could not store it.
UNWRITABLE_BODY: dict[str, Any] = {"serverUrl": "spec-audit-not-a-url"}


def test_set_metrics_server_url_with_stored_value_succeeds(
    config_client: ConfigClient, metrics_collection_snapshot: MetricsCollectionConfig
) -> None:
    stored = metrics_collection_snapshot.get("serverUrl")
    if not stored:
        pytest.skip("no metrics serverUrl is stored; writing one could not be undone")

    resp = config_client.patch(PATH, json={"serverUrl": stored})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Metrics collection remote server set successfully"}

    after = config_client.get("/metricsCollection")
    assert after.status_code == 200, after.text[:500]
    assert after.json() == metrics_collection_snapshot


def test_set_metrics_server_url_requires_token(config_client: ConfigClient) -> None:
    resp = config_client.patch(PATH, auth=False, json=UNWRITABLE_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_metrics_server_url_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PATCH", PATH, json=UNWRITABLE_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [UNWRITABLE_BODY, {}],
    ids=["not_a_url", "missing_server_url"],
)
def test_set_metrics_server_url_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.patch(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
