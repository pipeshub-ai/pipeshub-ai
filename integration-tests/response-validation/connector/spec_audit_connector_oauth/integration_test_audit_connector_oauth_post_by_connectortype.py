"""Strict OpenAPI audit of POST /api/v1/oauth/:connectorType."""

from __future__ import annotations

from typing import Any

import pytest
from connector_oauth_audit_support import (
    SEED_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    created_config_id,
    oauth_config_body,
    request_as,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType"


def test_create_returns_essential_fields_without_secrets(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    body = oauth_config_body()

    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)

    try:
        # Node relays FastAPI's default status, so a create answers 200 rather than 201.
        assert resp.status_code == 200, resp.text[:500]
        payload = resp.json()
        assert payload["success"] is True
        created = payload["oauthConfig"]
        assert created["_id"]
        assert created["oauthInstanceName"] == body["oauthInstanceName"]
        assert created["connectorType"] == SEED_CONNECTOR_TYPE
        assert "config" not in created
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        config_id = created_config_id(resp)
        if config_id:
            connector_oauth_client.remove(SEED_CONNECTOR_TYPE, config_id)


def test_create_without_token_is_unauthorized(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, oauth_config_body(), auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_as_member_is_forbidden(second_user: Any) -> None:
    # Node has no admin gate here; the refusal is Python's, relayed through handleBackendError.
    resp = request_as(second_user, "POST", f"/{SEED_CONNECTOR_TYPE}", json=oauth_config_body())

    try:
        assert resp.status_code == 403, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        # Only reachable if the gate is missing; the member cannot delete, the admin can.
        config_id = created_config_id(resp)
        if config_id:
            pytest.fail(f"member created OAuth config {config_id}; delete it as admin")


def test_create_without_instance_name_is_bad_request(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    body = oauth_config_body()
    del body["oauthInstanceName"]

    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)

    try:
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        config_id = created_config_id(resp)
        if config_id:
            connector_oauth_client.remove(SEED_CONNECTOR_TYPE, config_id)


def test_create_with_duplicate_name_is_conflict(
    connector_oauth_client: ConnectorOAuthClient,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    existing = seed_oauth_config()

    resp = connector_oauth_client.create(
        existing["connector_type"], oauth_config_body(existing["name"])
    )

    try:
        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        config_id = created_config_id(resp)
        if config_id:
            connector_oauth_client.remove(existing["connector_type"], config_id)
