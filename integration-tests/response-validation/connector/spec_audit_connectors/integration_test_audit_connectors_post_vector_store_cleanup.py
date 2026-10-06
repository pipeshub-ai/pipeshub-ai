"""Strict OpenAPI audit of POST /api/v1/connectors/vector-store/cleanup.

The success path is not run: it drops the records vector collection
every agent on this shared stack indexes into. Past the
feature flag the route is observed while this test holds the single-job lock,
which every job must take first, so nothing is started.
"""

from __future__ import annotations

import pytest
from connectors_audit_support import ConnectorsAuditClient, bearer, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/vector-store/cleanup"
PATH = "/vector-store/cleanup"


@pytest.mark.usefixtures("vector_store_rebuild_busy")
def test_cleanup_while_another_job_holds_the_lock_is_a_conflict(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(PATH)
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "already running" in resp.json()["error"]["message"], resp.text[:500]


@pytest.mark.usefixtures("vector_store_rebuild_busy")
def test_cleanup_ignores_a_request_body_and_query(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # No validator: the gateway forwards an empty body whatever was sent, so these reach
    # the same lock check as a bare call.
    with outside_request_contract("the route takes no parameters and discards the body"):
        resp = connectors_client.post(PATH, params={"dryRun": "true"}, json={"connectorIds": ["x"]})
        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.usefixtures("vector_store_rebuild_disabled")
def test_cleanup_while_rebuild_is_disabled_is_forbidden(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "disabled" in resp.json()["error"]["message"], resp.text[:500]


def test_member_is_refused_by_the_admin_check(second_user: SecondUser) -> None:
    # userAdminCheck sits before requireScopes and the proxy, so nothing reaches Python.
    resp = request_as(second_user, "POST", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_cleanup_without_the_connector_sync_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.post(PATH, auth=False, headers=bearer(token_without_connector_scopes))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "scope" in resp.json()["error"]["message"].lower(), resp.text[:500]


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="garbage-token"),
    ],
)
def test_cleanup_without_a_valid_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient, headers: dict[str, str] | None
) -> None:
    resp = connectors_client.post(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
