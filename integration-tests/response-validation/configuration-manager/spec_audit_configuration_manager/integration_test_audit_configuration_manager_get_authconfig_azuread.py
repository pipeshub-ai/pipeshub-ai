"""Strict OpenAPI audit of GET /api/v1/configurationManager/authConfig/azureAd."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/azureAd"
PATH = "/authConfig/azureAd"

# What setAzureAdAuthConfig encrypts into the store; the GET returns it verbatim.
STORED_KEYS = {"clientId", "tenantId", "authority", "enableJit"}


def test_admin_reads_stored_config_or_empty_object(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert isinstance(body, dict)
    # {} when the org never configured Azure AD.
    if body:
        assert set(body) <= STORED_KEYS, f"unexpected keys: {sorted(set(body) - STORED_KEYS)}"
        assert isinstance(body["clientId"], str) and body["clientId"]
        assert body["authority"] == f"https://login.microsoftonline.com/{body['tenantId']}"


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
