"""Strict OpenAPI audit of DELETE /api/v1/configurationManager/web-search/providers/:providerKey.

The usage check is by provider type across the whole org, so a stored duckduckgo provider
cannot be deleted while any agent searches with duckduckgo.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from configuration_manager_audit_support import (
    BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    INVALID_BEARER_HEADERS,
    MISSING_WEB_SEARCH_PROVIDER_KEY,
    SeedWebSearchProvider,
    request_as,
)
from helper.clients.agents_client import AgentsClient
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/providers/:providerKey"


def _listed(config_client: ConfigClient) -> dict[str, dict[str, Any]]:
    resp = config_client.get("/web-search")
    assert resp.status_code == 200, resp.text[:500]
    return {p["providerKey"]: p for p in resp.json()["providers"]}


def test_deleting_the_default_provider_hands_the_default_on(
    config_client: ConfigClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    first = seed_web_search_provider(isDefault=True)["providerKey"]
    second = seed_web_search_provider()["providerKey"]

    resp = config_client.delete(f"/web-search/providers/{first}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Web search provider deleted successfully",
        "details": {"providerKey": first, "provider": "duckduckgo", "wasDefault": True},
    }
    listed = _listed(config_client)
    assert first not in listed
    stored = [key for key in listed if key != BUILTIN_WEB_SEARCH_PROVIDER_KEY]
    # The first remaining stored provider takes the default.
    assert listed[stored[0]]["isDefault"] is True
    assert second in listed


def test_a_provider_an_agent_searches_with_cannot_be_deleted(
    config_client: ConfigClient, agents_client: AgentsClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    key = seed_web_search_provider()["providerKey"]
    created = agents_client.create_agent(
        name=f"spec-audit-web-search-{uuid.uuid4().hex[:8]}", webSearch={"provider": "duckduckgo"}
    )
    assert created.status_code == 201, created.text[:500]
    agent_key = created.json()["agent"]["_key"]
    try:
        resp = config_client.delete(f"/web-search/providers/{key}")

        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        assert body["status"] == "error"
        assert agent_key in [agent["_key"] for agent in body["agents"]]
        assert body["message"].startswith("Cannot delete this provider because it is currently used by")
        assert key in _listed(config_client)
    finally:
        deleted = agents_client.delete_agent(agent_key)
        assert deleted.status_code in (200, 204, 404), deleted.text[:300]


@pytest.mark.parametrize(
    "provider_key",
    # duckduckgo is listed by GET /web-search but never stored, so it cannot be deleted either.
    [MISSING_WEB_SEARCH_PROVIDER_KEY, BUILTIN_WEB_SEARCH_PROVIDER_KEY],
    ids=["unknown_key", "builtin_duckduckgo"],
)
def test_key_that_is_not_stored_is_not_found(
    config_client: ConfigClient, provider_key: str
) -> None:
    before = _listed(config_client)

    resp = config_client.delete(f"/web-search/providers/{provider_key}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "error"
    # Two 404 branches: nothing stored at all, or the key is absent from the stored list.
    assert body["message"] in (
        "No web search configuration found",
        f"Provider with key '{provider_key}' not found",
    )
    assert _listed(config_client).keys() == before.keys()


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "DELETE", f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}"
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_token_without_config_write_scope_reaches_the_lookup(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    # API bug: no requireScopes on this route, so the scope-less token gets as far as the 404.
    resp = config_client.delete(
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}", auth=False, headers=narrow_scope_headers
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
