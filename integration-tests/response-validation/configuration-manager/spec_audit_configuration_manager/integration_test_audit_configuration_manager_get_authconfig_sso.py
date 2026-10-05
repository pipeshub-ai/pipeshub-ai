"""Strict OpenAPI audit of GET /api/v1/configurationManager/authConfig/sso."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/sso"
PATH = "/authConfig/sso"

# What setSsoAuthConfig encrypts into the store; the GET returns it verbatim, certificate included.
STORED_KEYS = {"certificate", "entryPoint", "emailKey", "enableJit", "samlPlatform"}


def test_admin_reads_stored_config_with_sp_entity_id(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert isinstance(body, dict)
    # spEntityId is added on every read, even when the org never configured SAML.
    assert isinstance(body.get("spEntityId"), str) and body["spEntityId"]
    stored = set(body) - {"spEntityId"}
    assert stored <= STORED_KEYS, f"unexpected keys: {sorted(stored - STORED_KEYS)}"
    if stored:
        assert isinstance(body["certificate"], str) and body["certificate"]
        assert isinstance(body["entryPoint"], str) and body["entryPoint"]
        assert isinstance(body["enableJit"], bool)


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
