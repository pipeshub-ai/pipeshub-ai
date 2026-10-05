"""Strict OpenAPI audit of GET /api/v1/configurationManager/platform/feature-flags/effective."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/platform/feature-flags/effective"
PATH = "/platform/feature-flags/effective"
AVAILABLE_ROUTE = "/api/v1/configurationManager/platform/feature-flags/available"
AVAILABLE_PATH = "/platform/feature-flags/available"


def test_admin_reads_every_known_flag_as_boolean(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    # Only the flag map: fileUploadMaxSizeBytes from the same store must not ride along.
    assert list(body) == ["featureFlags"], body
    flags = body["featureFlags"]
    assert flags, "every known flag is seeded with its default, so the map is never empty"
    assert all(isinstance(value, bool) for value in flags.values()), flags

    available = config_client.get(AVAILABLE_PATH)
    assert available.status_code == 200, available.text[:500]
    assert_strict_openapi_response(available, AVAILABLE_ROUTE)
    # /available hides some flags; /effective resolves hidden ones too.
    toggleable = {flag["key"] for flag in available.json()["flags"]}
    assert toggleable <= set(flags), toggleable - set(flags)


def test_member_reads_same_flags_as_admin(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    # No userAdminCheck and no requireScopes here, unlike the sibling /available route.
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    admin = config_client.get(PATH)
    assert admin.status_code == 200, admin.text[:500]
    assert_strict_openapi_response(admin, ROUTE)
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
    assert_strict_openapi_response(resp, ROUTE)
