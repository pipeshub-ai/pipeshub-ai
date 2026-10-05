"""Strict OpenAPI audit of POST /api/v1/configurationManager/authConfig/oauth.

Negative paths only: a successful call replaces the org-wide OAuth sign-in config.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/oauth"
PATH = "/authConfig/oauth"

VALID_BODY: dict[str, Any] = {
    "providerName": "spec-audit",
    "clientId": "spec-audit-client-id",
    "authorizationUrl": "https://idp.spec-audit.invalid/authorize",
    "tokenEndpoint": "https://idp.spec-audit.invalid/token",
}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_oauth_config_requires_valid_token(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_oauth_config_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before zod, so even this valid body never reaches the store.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        {"providerName": "spec-audit"},
        {**VALID_BODY, "tokenEndpoint": "not-a-url"},
    ],
    ids=["missing_client_id", "token_endpoint_not_a_url"],
)
def test_oauth_config_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
