"""Strict OpenAPI audit of PUT /api/v1/configurationManager/web-search/default/:providerKey."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    INVALID_BEARER_HEADERS,
    MISSING_WEB_SEARCH_PROVIDER_KEY,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/default/:providerKey"


def test_set_builtin_provider_as_default_when_it_already_is(config_client: ConfigClient) -> None:
    listed = config_client.get("/web-search")
    assert listed.status_code == 200, listed.text[:500]
    providers = listed.json()["providers"]
    builtin = next(p for p in providers if p.get("providerKey") == BUILTIN_WEB_SEARCH_PROVIDER_KEY)
    if not builtin["isDefault"]:
        # Choosing duckduckgo clears a stored provider's default flag; setting it back runs
        # a health check against an external search service, so it cannot be restored here.
        pytest.skip("a stored web search provider is the default")

    resp = config_client.put(f"/web-search/default/{BUILTIN_WEB_SEARCH_PROVIDER_KEY}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success"
    assert body["details"] == {
        "providerKey": BUILTIN_WEB_SEARCH_PROVIDER_KEY,
        "provider": BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    }

    after = config_client.get("/web-search")
    assert after.status_code == 200, after.text[:500]
    assert after.json()["providers"] == providers


def test_set_unknown_provider_as_default_is_not_found(config_client: ConfigClient) -> None:
    # 404 whether or not any web search config is stored; only the message differs.
    resp = config_client.put(f"/web-search/default/{MISSING_WEB_SEARCH_PROVIDER_KEY}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["status"] == "error"


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_BEARER_HEADERS, id="invalid_token")],
)
def test_set_default_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.put(
        f"/web-search/default/{BUILTIN_WEB_SEARCH_PROVIDER_KEY}", auth=False, headers=headers
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_default_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", f"/web-search/default/{BUILTIN_WEB_SEARCH_PROVIDER_KEY}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
