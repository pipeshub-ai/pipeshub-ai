"""Strict OpenAPI audit of PUT /api/v1/configurationManager/web-search/default/:providerKey."""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    INVALID_BEARER_HEADERS,
    MISSING_WEB_SEARCH_PROVIDER_KEY,
    SeedWebSearchProvider,
    request_as,
    retry_while_health_check_times_out,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/default/:providerKey"


def _defaults(config_client: ConfigClient) -> dict[str, bool]:
    resp = config_client.get("/web-search")
    assert resp.status_code == 200, resp.text[:500]
    return {p["providerKey"]: p["isDefault"] for p in resp.json()["providers"]}


def test_a_stored_provider_becomes_the_default_after_its_health_check(
    config_client: ConfigClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    seed_web_search_provider(isDefault=True)
    key = seed_web_search_provider()["providerKey"]

    resp = retry_while_health_check_times_out(lambda: config_client.put(f"/web-search/default/{key}", timeout=90))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Default web search provider updated successfully",
        "details": {"providerKey": key, "provider": "duckduckgo"},
    }
    defaults = _defaults(config_client)
    assert [k for k, is_default in defaults.items() if is_default] == [key]


def test_choosing_the_builtin_provider_clears_every_stored_default(
    config_client: ConfigClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    key = seed_web_search_provider(isDefault=True)["providerKey"]

    resp = config_client.put(f"/web-search/default/{BUILTIN_WEB_SEARCH_PROVIDER_KEY}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["details"] == {
        "providerKey": BUILTIN_WEB_SEARCH_PROVIDER_KEY,
        "provider": BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    }
    defaults = _defaults(config_client)
    assert defaults[key] is False
    assert defaults[BUILTIN_WEB_SEARCH_PROVIDER_KEY] is True


def test_set_unknown_provider_as_default_is_not_found(config_client: ConfigClient) -> None:
    # 404 whether or not any web search config is stored; only the message differs.
    resp = config_client.put(f"/web-search/default/{MISSING_WEB_SEARCH_PROVIDER_KEY}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body: dict[str, Any] = resp.json()
    assert body["status"] == "error"


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_default_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", f"/web-search/default/{BUILTIN_WEB_SEARCH_PROVIDER_KEY}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_token_without_config_write_scope_reaches_the_lookup(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    # API bug: no requireScopes on this route, so the scope-less token gets as far as the 404.
    resp = config_client.put(
        f"/web-search/default/{MISSING_WEB_SEARCH_PROVIDER_KEY}", auth=False, headers=narrow_scope_headers
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
