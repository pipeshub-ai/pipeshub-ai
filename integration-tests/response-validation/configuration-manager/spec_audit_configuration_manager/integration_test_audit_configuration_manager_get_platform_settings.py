"""Strict OpenAPI audit of GET /api/v1/configurationManager/platform/settings.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> getPlatformSettings.
The handler reads the stored settings and fills every known feature flag the store lacks
with its default; it never returns the ``updatedAt`` the POST stores.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/platform/settings"
PATH = "/platform/settings"
AVAILABLE_PATH = "/platform/feature-flags/available"
EFFECTIVE_ROUTE = "/api/v1/configurationManager/platform/feature-flags/effective"
SPEC_AUDIT_FLAG = "SPEC_AUDIT_GET_PLATFORM_SETTINGS_FLAG"


@pytest.fixture
def current(config_client: ConfigClient) -> Iterator[dict[str, Any]]:
    """The settings in force; posted back after the test."""
    before = config_client.get(PATH)
    assert before.status_code == 200, before.text[:500]
    body: dict[str, Any] = before.json()
    try:
        yield dict(body)
    finally:
        restored = config_client.post(PATH, json=body)
        assert restored.status_code == 200, f"platform settings not restored: {restored.text[:500]}"
        assert config_client.get(PATH).json() == body


def test_admin_reads_the_upload_limit_and_every_known_flag(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"fileUploadMaxSizeBytes", "featureFlags"}, body
    assert isinstance(body["fileUploadMaxSizeBytes"], int) and body["fileUploadMaxSizeBytes"] > 0
    assert all(isinstance(value, bool) for value in body["featureFlags"].values())
    available = config_client.get(AVAILABLE_PATH)
    assert available.status_code == 200, available.text[:500]
    assert {flag["key"] for flag in available.json()["flags"]} <= set(body["featureFlags"])


def test_a_stored_flag_nobody_defined_is_returned_too(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    saved = config_client.post(
        PATH, json={**current, "featureFlags": {**current["featureFlags"], SPEC_AUDIT_FLAG: True}}
    )
    assert saved.status_code == 200, saved.text[:500]

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["featureFlags"][SPEC_AUDIT_FLAG] is True
    assert "updatedAt" not in resp.json()
    effective = config_client.get("/platform/feature-flags/effective")
    assert effective.status_code == 200, effective.text[:500]
    assert_strict_openapi_exchange(effective, EFFECTIVE_ROUTE)
    assert effective.json()["featureFlags"][SPEC_AUDIT_FLAG] is True


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
