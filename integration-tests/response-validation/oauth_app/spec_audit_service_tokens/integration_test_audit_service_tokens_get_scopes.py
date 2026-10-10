"""Strict OpenAPI audit of GET /api/v1/service-tokens/scopes."""

from __future__ import annotations

import pytest
from service_tokens_audit_support import (
    DEFAULT_TOKEN_SCOPES,
    DENIED_TOKEN_SCOPE,
    ServiceTokensClient,
    request_as,
    request_with_token,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-tokens/scopes"
SCOPE_DEFINITION_FIELDS = {"name", "description", "category", "requiresUserConsent"}


def test_admin_lists_scope_definitions_without_the_denied_scope(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.list_scopes()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"scopes"}
    scopes = body["scopes"]
    assert isinstance(scopes, list)
    for definition in scopes:
        assert set(definition) == SCOPE_DEFINITION_FIELDS, definition
        assert isinstance(definition["name"], str)
        assert isinstance(definition["description"], str)
        assert isinstance(definition["category"], str)
        assert isinstance(definition["requiresUserConsent"], bool)

    names = [definition["name"] for definition in scopes]
    assert len(names) == len(set(names))
    assert DENIED_TOKEN_SCOPE not in names
    assert set(DEFAULT_TOKEN_SCOPES) <= set(names)


def test_list_scopes_without_token_is_unauthorized(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.list_scopes(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_scopes_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/scopes")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_scopes_ignores_query_parameters(
    service_tokens_client: ServiceTokensClient,
) -> None:
    expected = service_tokens_client.list_scopes().json()

    with outside_request_contract("an undocumented query parameter; the handler reads no query"):
        resp = service_tokens_client.get("/scopes", params={"includeDenied": "true"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == expected


def test_list_scopes_with_a_token_lacking_user_read_is_forbidden(
    service_tokens_client: ServiceTokensClient, kb_read_pat: str
) -> None:
    resp = request_with_token(
        service_tokens_client._client.base_url, kb_read_pat, "GET", "/scopes"
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)
