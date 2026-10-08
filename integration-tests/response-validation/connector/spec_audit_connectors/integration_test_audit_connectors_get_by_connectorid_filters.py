"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/filters."""

from __future__ import annotations

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
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/filters"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/filters"


def test_admin_reads_filter_options_of_a_configured_connector(
    connectors_client: ConnectorsAuditClient, bookstack_connector: str
) -> None:
    resp = connectors_client.get(_path(bookstack_connector))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # No registered connector type declares filter endpoints, so the map is empty;
    # the filter fields themselves come from the registry schema.
    assert resp.json() == {"success": True, "filterOptions": {}}


def test_member_reads_filter_options_of_own_personal_connector(
    second_user: SecondUser, member_configured_connector: str
) -> None:
    resp = request_as(second_user, "GET", _path(member_configured_connector))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "filterOptions": {}}


@pytest.mark.parametrize(
    ("create", "reason"),
    [
        # authType NONE has no credentials to fetch options with.
        pytest.param({}, "Unsupported authentication type: NONE", id="auth-type-none"),
        pytest.param(
            {"connectorType": "BookStack", "authType": "API_TOKEN"},
            "Configuration not found. Please configure first.",
            id="api-token-not-configured",
        ),
        pytest.param(
            {"connectorType": "GitLab", "authType": "OAUTH"},
            "OAuth credentials not found. Please authenticate first.",
            id="oauth-not-authenticated",
        ),
    ],
)
def test_filters_of_connector_without_credentials_is_bad_request(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector, create: dict[str, str], reason: str
) -> None:
    resp = connectors_client.get(_path(seed_connector(**create)))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == reason


def test_filters_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(_path(connector_id), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_filters_without_the_read_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.get(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_see_filters_of_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry read gate hides the instance from a non-creator member, so the
    # handler's own 403 permission check is never reached.
    resp = request_as(second_user, "GET", _path(connector_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        pytest.param(MALFORMED_CONNECTOR_ID, 400, id="id-fails-param-schema"),
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_filters_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient,
    requested_id: str,
    expected_status: int,
) -> None:
    resp = connectors_client.get(_path(requested_id))
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
