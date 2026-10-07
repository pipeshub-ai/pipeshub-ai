"""Strict OpenAPI audit of GET /api/v1/configurationManager/connectorPublicUrl."""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectorPublicUrl"
PATH = "/connectorPublicUrl"


def _assert_url_or_empty(body: Any) -> None:
    # The controller answers {} when no connector endpoint is stored, else exactly {url}.
    assert isinstance(body, dict), body
    if body:
        assert list(body) == ["url"], body
        assert isinstance(body["url"], str) and body["url"], body


def test_admin_reads_connector_public_url(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    _assert_url_or_empty(resp.json())


def test_member_reads_same_url_as_admin(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    # Unlike POST on this path, the GET has no userAdminCheck.
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    _assert_url_or_empty(resp.json())

    admin = config_client.get(PATH)
    assert admin.status_code == 200, admin.text[:500]
    assert_strict_openapi_exchange(admin, ROUTE)
    assert resp.json() == admin.json()


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_read_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.get("/connectorPublicUrl", auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
