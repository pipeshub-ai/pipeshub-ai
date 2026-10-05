"""Strict OpenAPI audit of PUT /api/v1/connectors/:connectorId/config."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    ConnectorsAuditClient,
    SeedConnector,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/config"

# No "sync" section: a saved sync block makes Node reconcile the crawl schedule.
INDEXING_ONLY_BODY: dict[str, Any] = {"filters": {"indexing": {}}}


def _path(connector_id: str) -> str:
    return f"/{connector_id}/config"


def test_update_config_as_admin_returns_the_merged_config(
    connectors_client: ConnectorsAuditClient,
    seed_connector: SeedConnector,
) -> None:
    resp = connectors_client.put(_path(seed_connector()), json=INDEXING_ONLY_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert body["config"]["filters"]["indexing"] == {}, body
    assert body["message"] == "Configuration saved successfully.", body


def test_update_config_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
) -> None:
    resp = connectors_client.put(
        _path(connector_id), auth=False, json=INDEXING_ONLY_BODY
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_config_of_a_team_connector_as_member_is_404(
    second_user: SecondUser,
    connector_id: str,
) -> None:
    # The registry hides a team instance from a non-admin who did not create it,
    # so the handler's own 403 "Only administrators..." branch is never reached.
    resp = request_as(
        second_user, "PUT", _path(connector_id), json=INDEXING_ONLY_BODY
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"baseUrl": 123}, id="node-zod-baseurl-not-a-string"),
        pytest.param({"filters": {"sync": None}}, id="python-filters-sync-not-an-object"),
    ],
)
def test_update_config_with_an_invalid_body_is_400(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    body: dict[str, Any],
) -> None:
    resp = connectors_client.put(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
