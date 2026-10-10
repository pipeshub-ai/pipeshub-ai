"""Strict OpenAPI audit of POST /api/v1/connectors/:connectorId/filters."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/filters"

FILTER_SELECTIONS: dict[str, Any] = {"spec_audit_filter": ["one", "two"]}


def _path(connector_id: str) -> str:
    return f"/{connector_id}/filters"


def test_admin_saves_filter_selections(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    seeded_id = seed_connector()

    resp = connectors_client.post(_path(seeded_id), json={"filters": FILTER_SELECTIONS})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "message": "Filter selections saved successfully"}


@pytest.mark.parametrize(
    "filters",
    [
        pytest.param("spec-audit", id="a-string"),
        pytest.param(["spec-audit"], id="a-list"),
    ],
)
def test_filters_that_are_not_an_object_are_saved_too(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector, filters: Any
) -> None:
    # Any truthy value passes Node and is stored as sent.
    with outside_request_contract("filters is not an object on purpose"):
        resp = connectors_client.post(_path(seed_connector()), json={"filters": filters})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


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
    resp = connectors_client.post(_path(connector_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_save_filters_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(connector_id), auth=False, json={"filters": FILTER_SELECTIONS})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_save_filters_without_the_write_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.post(
        _path(connector_id),
        auth=False,
        headers=bearer(token_without_connector_scopes),
        json={"filters": FILTER_SELECTIONS},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_see_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides the instance from a non-admin non-creator, so
    # the handler answers 404 before its own 403 permission check can run.
    resp = request_as(second_user, "POST", _path(connector_id), json={"filters": FILTER_SELECTIONS})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        # Unlike GET on the same path, this route does not check the id's shape.
        pytest.param(MALFORMED_CONNECTOR_ID, 404, id="id-the-get-route-rejects"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_save_filters_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient, requested_id: str, expected_status: int
) -> None:
    resp = connectors_client.post(_path(requested_id), json={"filters": FILTER_SELECTIONS})
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
