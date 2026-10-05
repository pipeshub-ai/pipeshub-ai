"""Strict OpenAPI audit of DELETE /api/v1/configurationManager/web-search/providers/:providerKey.

No success case: a stored provider can only be seeded through POST /web-search/providers,
which runs a health check against an external search service.
"""

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

ROUTE = "/api/v1/configurationManager/web-search/providers/:providerKey"


def _provider_keys(config_client: ConfigClient) -> list[str]:
    resp = config_client.get("/web-search")
    assert resp.status_code == 200, resp.text[:500]
    return [provider["providerKey"] for provider in resp.json()["providers"]]


@pytest.mark.parametrize(
    "provider_key",
    # duckduckgo is listed by GET /web-search but never stored, so it cannot be deleted either.
    [MISSING_WEB_SEARCH_PROVIDER_KEY, BUILTIN_WEB_SEARCH_PROVIDER_KEY],
    ids=["unknown_key", "builtin_duckduckgo"],
)
def test_key_that_is_not_stored_is_not_found(
    config_client: ConfigClient, provider_key: str
) -> None:
    before = _provider_keys(config_client)

    resp = config_client.delete(f"/web-search/providers/{provider_key}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "error"
    # Two 404 branches: nothing stored at all, or the key is absent from the stored list.
    assert body["message"] in (
        "No web search configuration found",
        f"Provider with key '{provider_key}' not found",
    )
    assert _provider_keys(config_client) == before


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.delete(
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}",
        auth=False,
        headers=headers,
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "DELETE", f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}"
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
