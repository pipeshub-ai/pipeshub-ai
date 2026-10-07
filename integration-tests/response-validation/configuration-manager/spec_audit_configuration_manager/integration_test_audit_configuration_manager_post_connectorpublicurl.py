"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectorPublicUrl.

A successful call rewrites the org-wide connector OAuth callback base URL and publishes
a ConnectorPublicUrlChanged event; the stored endpoints are put back byte for byte afterwards.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_ENDPOINTS,
    GuardStoredValue,
    assert_validation_error,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectorPublicUrl"

# Port 9 (discard): if it leaked, callbacks would lead nowhere rather than somewhere real.
SPEC_AUDIT_URL = "http://127.0.0.1:9/spec-audit-connectors"
VALID_BODY: dict[str, Any] = {"url": "https://connectors.spec-audit.example.com"}


def test_set_connector_public_url_saves_it_without_the_trailing_slash(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_ENDPOINTS)
    frontend_before = config_client.get("/frontendPublicUrl")
    assert frontend_before.status_code == 200, frontend_before.text[:500]

    resp = config_client.post("/connectorPublicUrl", json={"url": f"{SPEC_AUDIT_URL}/"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "Connector Url saved successfully"}
    stored = config_client.get("/connectorPublicUrl")
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {"url": SPEC_AUDIT_URL}
    # Both URLs live in one stored object; saving one keeps the other.
    assert config_client.get("/frontendPublicUrl").json() == frontend_before.json()


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(None, id="no-token"),
        pytest.param(INVALID_BEARER_HEADERS, id="invalid-token"),
    ],
)
def test_set_connector_public_url_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post("/connectorPublicUrl", auth=False, headers=headers, json=VALID_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_connector_public_url_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/connectorPublicUrl", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-url"),
        # z.string().url() needs a scheme; the controller's own URL check is never reached.
        pytest.param({"url": "connectors.spec-audit.example.com"}, id="url-without-scheme"),
        pytest.param({"url": ""}, id="empty-url"),
        pytest.param({"url": None}, id="url-null"),
    ],
)
def test_set_connector_public_url_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post("/connectorPublicUrl", json=body)

    assert_validation_error(resp, "body.url")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.post("/connectorPublicUrl", auth=False, headers=narrow_scope_headers, json={"url": "https://spec-audit.pipeshub.invalid"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
