"""Strict OpenAPI audit of POST /api/v1/configurationManager/connectorPublicUrl.

Negative paths only: a successful call rewrites the org-wide connector OAuth callback
base URL and publishes a ConnectorPublicUrlChanged event, and no route can clear the
value again when none was stored before.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectorPublicUrl"

VALID_BODY: dict[str, Any] = {"url": "https://connectors.spec-audit.example.com"}


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
    assert_strict_openapi_response(resp, ROUTE)


def test_set_connector_public_url_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/connectorPublicUrl", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-url"),
        # z.string().url() needs a scheme; the controller's own URL check is never reached.
        pytest.param({"url": "connectors.spec-audit.example.com"}, id="url-without-scheme"),
    ],
)
def test_set_connector_public_url_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post("/connectorPublicUrl", json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
