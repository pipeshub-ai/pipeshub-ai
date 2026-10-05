"""Strict OpenAPI audit of GET /api/v1/configurationManager/authConfig/oauth."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/oauth"
PATH = "/authConfig/oauth"

# What setOAuthConfig encrypts into the store; the GET returns it verbatim, secret included.
ALWAYS_STORED_KEYS = {"providerName", "clientId", "enableJit"}
OPTIONAL_STORED_KEYS = {
    "clientSecret",
    "authorizationUrl",
    "tokenEndpoint",
    "userInfoEndpoint",
    "scope",
    "redirectUri",
}


def test_admin_reads_stored_config_or_empty_object(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert isinstance(body, dict)
    # {} when the org never configured a generic OAuth provider.
    if body:
        allowed = ALWAYS_STORED_KEYS | OPTIONAL_STORED_KEYS
        assert set(body) <= allowed, f"unexpected keys: {sorted(set(body) - allowed)}"
        assert ALWAYS_STORED_KEYS <= set(body), f"missing keys: {sorted(ALWAYS_STORED_KEYS - set(body))}"
        assert isinstance(body["providerName"], str) and body["providerName"]
        assert isinstance(body["clientId"], str) and body["clientId"]
        assert isinstance(body["enableJit"], bool)
        # The setter drops empty optionals instead of storing "".
        for key in OPTIONAL_STORED_KEYS & set(body):
            assert isinstance(body[key], str) and body[key], f"{key} stored empty"


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_unauthenticated_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
