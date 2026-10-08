"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/registry.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> proxy to the query
service's GET /api/v1/ai-models/registry, whose status and body are relayed as they are.
Neither side validates the query: ``search`` and ``capability`` are plain filters.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    assert_strict_openapi_response_keeping_field_names,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, assert_strict_openapi_request

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/registry"
PATH = "/ai-models/registry"


def test_admin_lists_every_registered_provider(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    # Some field descriptors carry an "examples" list, which the shared check cannot see in the spec.
    assert_strict_openapi_response_keeping_field_names(resp, ROUTE)
    assert_strict_openapi_request(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"success", "providers", "total"}
    assert body["success"] is True
    # The registry is filled at import time on the Python side, so it is never empty.
    assert body["providers"], "the provider registry came back empty"
    assert body["total"] == len(body["providers"])
    ids = [provider["providerId"] for provider in body["providers"]]
    assert all(ids) and len(ids) == len(set(ids)), ids


def test_capability_filter_keeps_only_matching_providers(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH, params={"capability": "embedding"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response_keeping_field_names(resp, ROUTE)
    assert_strict_openapi_request(resp, ROUTE)
    body = resp.json()
    assert body["providers"], "no provider advertises the embedding capability"
    assert body["total"] == len(body["providers"])
    assert all("embedding" in provider["capabilities"] for provider in body["providers"])


def test_search_matches_the_provider_name_in_any_case(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH, params={"search": "OPENAI", "capability": "text_generation"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    ids = {provider["providerId"] for provider in resp.json()["providers"]}
    assert "openAI" in ids, ids


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"capability": "spec-audit-no-such-capability"}, id="unknown-capability"),
        pytest.param({"search": "a", "capability": "spec-audit-no-such-capability"}, id="search-and-unknown-capability"),
        pytest.param({"search": "spec-audit-no-such-provider"}, id="search-without-match"),
    ],
)
def test_a_filter_that_matches_nothing_is_an_empty_list_not_a_400(
    config_client: ConfigClient, params: dict[str, str]
) -> None:
    resp = config_client.get(PATH, params=params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "providers": [], "total": 0}


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
