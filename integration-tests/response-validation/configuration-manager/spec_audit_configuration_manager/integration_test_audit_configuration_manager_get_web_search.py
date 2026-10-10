"""Strict OpenAPI audit of GET /api/v1/configurationManager/web-search."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    INVALID_BEARER_HEADERS,
    SECRET_PLACEHOLDER,
    SeedWebSearchProvider,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search"


def test_admin_lists_the_builtin_provider_and_the_settings(config_client: ConfigClient) -> None:
    resp = config_client.get("/web-search")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    # duckduckgo is always listed first, whether or not anything is stored.
    assert body["providers"][0]["providerKey"] == BUILTIN_WEB_SEARCH_PROVIDER_KEY
    assert body["providers"][0]["configuration"] == {}
    assert sum(1 for p in body["providers"] if p["isDefault"]) == 1
    assert set(body["settings"]) == {"includeImages", "maxImages"}


def test_a_stored_provider_takes_the_default_and_shows_its_key_to_admins_only(
    config_client: ConfigClient, second_user: SecondUser, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    api_key = "spec-audit-visible-to-admins"
    seeded = seed_web_search_provider(configuration={"apiKey": api_key}, isDefault=True)

    admin = config_client.get("/web-search")
    assert admin.status_code == 200, admin.text[:500]
    assert_strict_openapi_exchange(admin, ROUTE)
    providers = {p["providerKey"]: p for p in admin.json()["providers"]}
    assert providers[seeded["providerKey"]]["configuration"] == {"apiKey": api_key}
    assert providers[seeded["providerKey"]]["isDefault"] is True
    assert providers[BUILTIN_WEB_SEARCH_PROVIDER_KEY]["isDefault"] is False
    assert admin.json()["message"] == "Web search providers retrieved successfully"

    member = request_as(second_user, "GET", "/web-search")
    assert member.status_code == 200, member.text[:500]
    assert_strict_openapi_exchange(member, ROUTE)
    listed = {p["providerKey"]: p for p in member.json()["providers"]}
    assert listed[seeded["providerKey"]]["configuration"] == {"apiKey": SECRET_PLACEHOLDER}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_unauthenticated_is_rejected(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get("/web-search", auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_read_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.get("/web-search", auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
