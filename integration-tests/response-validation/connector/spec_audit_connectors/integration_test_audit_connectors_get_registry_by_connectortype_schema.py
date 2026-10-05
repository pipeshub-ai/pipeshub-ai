"""Strict OpenAPI audit of GET /api/v1/connectors/registry/:connectorType/schema."""

from __future__ import annotations

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/registry/:connectorType/schema"


def _path(connector_type: str) -> str:
    return f"/registry/{connector_type}/schema"


def test_schema_as_admin_returns_the_registry_config(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(SEED_CONNECTOR_TYPE))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert isinstance(body["schema"], dict), body


def test_schema_is_readable_by_a_non_admin_member(second_user: SecondUser) -> None:
    # No userAdminCheck on this route, in Node or in Python.
    resp = request_as(second_user, "GET", _path(SEED_CONNECTOR_TYPE))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert isinstance(resp.json()["schema"], dict), resp.text[:500]


def test_schema_of_an_unregistered_type_is_404(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(MISSING_CONNECTOR_TYPE))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_schema_without_a_token_is_401(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.get(_path(SEED_CONNECTOR_TYPE), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_schema_with_an_unsafe_type_is_400_before_auth(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers ahead of authenticate.
    resp = connectors_client.get(_path(UNSAFE_CONNECTOR_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
