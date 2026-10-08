"""Strict OpenAPI audit of PUT /api/v1/configurationManager/web-search/providers/:providerKey.

Updating a stored provider runs the health check again before anything is saved; the
stored provider is a duckduckgo one (its health check needs no account).
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    BUILTIN_WEB_SEARCH_PROVIDER_KEY,
    INVALID_BEARER_HEADERS,
    MISSING_WEB_SEARCH_PROVIDER_KEY,
    SECRET_PLACEHOLDER,
    SeedWebSearchProvider,
    assert_validation_error,
    request_as,
    retry_while_health_check_times_out,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/providers/:providerKey"

VALID_BODY: dict[str, Any] = {
    "provider": "serper",
    "configuration": {"apiKey": "spec-audit-not-a-real-key"},
}


def _listed(config_client: ConfigClient) -> dict[str, dict[str, Any]]:
    resp = config_client.get("/web-search")
    assert resp.status_code == 200, resp.text[:500]
    return {p["providerKey"]: p for p in resp.json()["providers"]}


def test_the_placeholder_keeps_the_stored_key(
    config_client: ConfigClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    api_key = "spec-audit-kept-key"
    seeded = seed_web_search_provider(configuration={"apiKey": api_key}, isDefault=True)
    key = seeded["providerKey"]

    resp = retry_while_health_check_times_out(
        lambda: config_client.put(
            f"/web-search/providers/{key}",
            json={"provider": "duckduckgo", "configuration": {"apiKey": SECRET_PLACEHOLDER, "region": "wt-wt"}, "isDefault": True},
            timeout=90,
        )
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Web search provider updated successfully",
        "details": {"providerKey": key, "provider": "duckduckgo"},
    }
    stored = _listed(config_client)[key]
    assert stored["configuration"] == {"apiKey": api_key, "region": "wt-wt"}
    assert stored["isDefault"] is True


def test_leaving_out_isdefault_takes_the_default_away(
    config_client: ConfigClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    key = seed_web_search_provider(isDefault=True)["providerKey"]

    resp = retry_while_health_check_times_out(
        lambda: config_client.put(
            f"/web-search/providers/{key}", json={"provider": "duckduckgo", "configuration": {}}, timeout=90
        )
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    listed = _listed(config_client)
    assert listed[key]["isDefault"] is False


def test_a_configuration_the_health_check_refuses_is_not_saved(
    config_client: ConfigClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    key = seed_web_search_provider()["providerKey"]
    before = _listed(config_client)[key]

    resp = config_client.put(f"/web-search/providers/{key}", json={"provider": "tavily", "configuration": {}})

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["message"] == "Tavily API key is required"
    assert _listed(config_client)[key] == before


@pytest.mark.parametrize(
    "provider_key",
    [
        pytest.param(MISSING_WEB_SEARCH_PROVIDER_KEY, id="unknown-key"),
        # duckduckgo is listed by GET /web-search but never stored, so it cannot be edited.
        pytest.param(BUILTIN_WEB_SEARCH_PROVIDER_KEY, id="builtin-duckduckgo"),
    ],
)
def test_update_unstored_provider_is_not_found(
    config_client: ConfigClient, provider_key: str
) -> None:
    # The lookup precedes the health check, so nothing external is called.
    resp = config_client.put(f"/web-search/providers/{provider_key}", json=VALID_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "error"
    assert body["message"] in (
        "No web search configuration found",
        f"Provider with key '{provider_key}' not found",
    )


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({**VALID_BODY, "provider": "spec-audit-no-such-engine"}, "body.provider", id="provider-outside-enum"),
        pytest.param({"provider": "serper"}, "body.configuration", id="configuration-missing"),
        pytest.param({**VALID_BODY, "isDefault": 1}, "body.isDefault", id="isDefault-not-boolean"),
    ],
)
def test_update_with_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any], field: str
) -> None:
    resp = config_client.put(f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}", json=body)
    assert_validation_error(resp, field)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(
        second_user,
        "PUT",
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}",
        json=VALID_BODY,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_update_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.put(
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}",
        auth=False,
        headers=headers,
        json=VALID_BODY,
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_token_without_config_write_scope_reaches_the_lookup(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    # API bug: no requireScopes on this route, so the scope-less token gets as far as the 404.
    resp = config_client.put(
        f"/web-search/providers/{MISSING_WEB_SEARCH_PROVIDER_KEY}", auth=False, headers=narrow_scope_headers, json=VALID_BODY
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
