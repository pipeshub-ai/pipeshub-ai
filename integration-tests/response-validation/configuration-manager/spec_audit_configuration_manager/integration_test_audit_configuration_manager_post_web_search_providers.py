"""Strict OpenAPI audit of POST /api/v1/configurationManager/web-search/providers.

A body that passes zod makes Node call the Python web-search health check before
anything is stored. duckduckgo passes it without an account; the stored web search
config is put back byte for byte afterwards.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_WEB_SEARCH,
    GuardStoredValue,
    SeedWebSearchProvider,
    assert_validation_error,
    request_as,
    retry_while_health_check_times_out,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/web-search/providers"
PATH = "/web-search/providers"

VALID_BODY: dict[str, Any] = {
    "provider": "serper",
    "configuration": {"apiKey": "spec-audit-not-a-real-key"},
}


def _stored_providers(config_client: ConfigClient) -> list[dict[str, Any]]:
    resp = config_client.get("/web-search")
    assert resp.status_code == 200, resp.text[:500]
    return [p for p in resp.json()["providers"] if p["providerKey"] != "duckduckgo"]


def test_add_a_provider_that_passes_its_health_check(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_WEB_SEARCH)
    nothing_stored = not _stored_providers(config_client)

    resp = retry_while_health_check_times_out(
        lambda: config_client.post(PATH, json={"provider": "duckduckgo", "configuration": {}}, timeout=90)
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["status"], body["message"]) == ("success", "Web search provider added successfully")
    details = body["details"]
    uuid.UUID(details["providerKey"])
    # The first stored provider becomes the default even when isDefault is left out.
    assert details == {"providerKey": details["providerKey"], "provider": "duckduckgo", "isDefault": nothing_stored}
    assert details["providerKey"] in [p["providerKey"] for p in _stored_providers(config_client)]


def test_a_later_provider_is_not_the_default_unless_asked(
    config_client: ConfigClient, seed_web_search_provider: SeedWebSearchProvider
) -> None:
    first = seed_web_search_provider(isDefault=True)

    resp = retry_while_health_check_times_out(
        lambda: config_client.post(PATH, json={"provider": "duckduckgo", "configuration": {}}, timeout=90)
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["details"]["isDefault"] is False
    flags = {p["providerKey"]: p["isDefault"] for p in _stored_providers(config_client)}
    assert flags[first["providerKey"]] is True


def test_a_configuration_the_health_check_refuses_is_not_stored(
    config_client: ConfigClient, guard_stored_value: GuardStoredValue
) -> None:
    guard_stored_value(KV_WEB_SEARCH)
    before = _stored_providers(config_client)

    # serper without an apiKey fails inside the health check, before any outside call.
    resp = config_client.post(PATH, json={"provider": "serper", "configuration": {}})

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["status"], body["message"]) == ("error", "Serper API key is required")
    assert body["details"]["status"] == "not healthy"
    assert _stored_providers(config_client) == before


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param(
            {"provider": "spec-audit-no-such-engine", "configuration": {"apiKey": "x"}},
            "body.provider",
            id="provider-outside-enum",
        ),
        pytest.param({"configuration": {}}, "body.provider", id="provider-missing"),
        pytest.param({"provider": "serper"}, "body.configuration", id="configuration-missing"),
        pytest.param({"provider": "serper", "configuration": "apiKey"}, "body.configuration", id="configuration-not-object"),
        pytest.param(
            {"provider": "serper", "configuration": {"apiKey": "x"}, "isDefault": "yes"},
            "body.isDefault",
            id="isDefault-not-boolean",
        ),
    ],
)
def test_add_web_search_provider_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any], field: str
) -> None:
    resp = config_client.post(PATH, json=body)
    assert_validation_error(resp, field)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_add_web_search_provider_requires_valid_token(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_add_web_search_provider_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck runs before zod and the health check, so this valid body goes nowhere.
    resp = request_as(second_user, "POST", PATH, json=VALID_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_token_without_config_write_scope_reaches_the_validator(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    # API bug: no requireScopes on this route, so the scope-less token gets past to zod.
    resp = config_client.post(PATH, auth=False, headers=narrow_scope_headers, json={"provider": "serper"})
    assert_validation_error(resp, "body.configuration")
    assert_strict_openapi_exchange(resp, ROUTE)
