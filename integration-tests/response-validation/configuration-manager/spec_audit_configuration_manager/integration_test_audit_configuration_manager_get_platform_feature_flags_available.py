"""Strict OpenAPI audit of GET /api/v1/configurationManager/platform/feature-flags/available."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/platform/feature-flags/available"
PATH = "/platform/feature-flags/available"

# Marked hidden in PLATFORM_FEATURE_FLAGS: seeded in the store, never listed.
HIDDEN_FLAG_KEY = "ENABLE_BETA_CONNECTORS"
ALWAYS_LISTED_FLAG_KEYS = {"ENABLE_MCP", "ENABLE_ACTIONS", "ENABLE_SKILLS"}


def test_admin_lists_toggleable_flags(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert list(body) == ["flags"], body
    flags = body["flags"]
    keys = [flag["key"] for flag in flags]
    assert len(keys) == len(set(keys)), keys
    assert HIDDEN_FLAG_KEY not in keys, keys
    assert ALWAYS_LISTED_FLAG_KEYS <= set(keys), keys
    for flag in flags:
        assert isinstance(flag["label"], str) and flag["label"], flag
        assert isinstance(flag["defaultEnabled"], bool), flag
        # The filter that drops hidden flags leaves no flag carrying a true marker.
        assert not flag.get("hidden"), flag


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


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
