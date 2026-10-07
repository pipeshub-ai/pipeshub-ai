"""Strict OpenAPI audit of GET /api/v1/connectors/:connectorId/oauth/authorize.

The success case uses a GitLab instance whose instance URL is a local stand-in
for the provider, so no third party is contacted.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from connectors_audit_support import (
    MALFORMED_CONNECTOR_ID,
    MISSING_CONNECTOR_ID,
    OAUTH_BASE_URL,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    SeedConnector,
    StubSource,
    bearer,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/:connectorId/oauth/authorize"


def _path(connector_id: str) -> str:
    return f"/{connector_id}/oauth/authorize"


def test_admin_gets_the_authorization_url_of_an_oauth_connector(
    connectors_client: ConnectorsAuditClient, gitlab_oauth_connector: str, oauth_provider: StubSource
) -> None:
    resp = connectors_client.get(_path(gitlab_oauth_connector), params={"baseUrl": OAUTH_BASE_URL})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    url = urlsplit(body["authorizationUrl"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{oauth_provider.url}/oauth/authorize"
    query = parse_qs(url.query)
    assert query["client_id"] == ["spec-audit-client"]
    assert query["state"] == [body["state"]]
    assert query["redirect_uri"] == [f"{OAUTH_BASE_URL}/connectors/oauth/callback/Gitlab"]


def test_authorize_on_connector_without_oauth_is_bad_request(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    # The seeded Demo instance has authType NONE; Python refuses before reading any config.
    resp = connectors_client.get(_path(connector_id), params={"baseUrl": "https://spec-audit.invalid"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == "Connector instance does not support OAuth"


def test_authorize_before_oauth_credentials_are_saved_is_bad_request(
    connectors_client: ConnectorsAuditClient, seed_connector: SeedConnector
) -> None:
    unconfigured = seed_connector(connectorType="GitLab", authType="OAUTH")
    resp = connectors_client.get(_path(unconfigured))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "configure first" in resp.json()["error"]["message"]


def test_authorize_without_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.get(_path(connector_id), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authorize_without_the_write_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    connector_id: str,
    token_without_connector_scopes: str,
) -> None:
    resp = connectors_client.get(
        _path(connector_id), auth=False, headers=bearer(token_without_connector_scopes)
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_see_admin_owned_team_connector(
    second_user: SecondUser, connector_id: str
) -> None:
    # The registry access gate hides the instance from a non-creator member, so the
    # handler answers 404 and never reaches its own "administrators only" 403.
    resp = request_as(second_user, "GET", _path(connector_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("requested_id", "expected_status"),
    [
        pytest.param(MISSING_CONNECTOR_ID, 404, id="unknown-connector"),
        pytest.param(MALFORMED_CONNECTOR_ID, 404, id="id-the-instance-routes-reject"),
        # Refused by guardPathParams; the route schema accepts any non-empty id.
        pytest.param(UNSAFE_CONNECTOR_ID, 400, id="unsafe-path-segment"),
    ],
)
def test_authorize_for_unusable_connector_id(
    connectors_client: ConnectorsAuditClient,
    requested_id: str,
    expected_status: int,
) -> None:
    resp = connectors_client.get(_path(requested_id))
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
