"""Strict OpenAPI audit of POST /api/v1/connectors/:connectorId/filters."""

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

ROUTE = "/api/v1/connectors/:connectorId/filters"

FILTER_SELECTIONS: dict[str, Any] = {"spec_audit_filter": ["one", "two"]}


def test_admin_saves_filter_selections(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    seeded_id = seed_connector()

    resp = connectors_client.post(
        f"/{seeded_id}/filters", json={"filters": FILTER_SELECTIONS}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["success"] is True


def test_save_filters_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(
        f"/{connector_id}/filters", auth=False, json={"filters": FILTER_SELECTIONS}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_see_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides the instance from a non-admin non-creator, so
    # the handler answers 404 before its own 403 permission check can run.
    resp = request_as(
        second_user,
        "POST",
        f"/{connector_id}/filters",
        json={"filters": FILTER_SELECTIONS},
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # zod declares filters as z.any(), so the controller is what refuses this.
        pytest.param({}, id="filters-missing-refused-by-node"),
        # Truthy in Node, so it is forwarded and Python refuses the empty selection.
        pytest.param({"filters": {}}, id="filters-empty-refused-by-python"),
    ],
)
def test_save_without_filter_selections_is_bad_request(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    body: dict[str, Any],
) -> None:
    resp = connectors_client.post(f"/{connector_id}/filters", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
