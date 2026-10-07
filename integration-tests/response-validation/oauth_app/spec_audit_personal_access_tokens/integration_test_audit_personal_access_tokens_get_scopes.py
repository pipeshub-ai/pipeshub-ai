"""Strict OpenAPI audit of GET /api/v1/personal-access-tokens/scopes."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.second_user import SecondUser
from personal_access_tokens_audit_support import (
    MintPat,
    PatsClient,
    request_as,
    request_with_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/personal-access-tokens/scopes"
SCOPE_FIELDS = {"name", "description", "category", "requiresUserConsent"}
SESSION_ONLY = "requires an interactive user session"


def _scope_definitions(resp: requests.Response) -> list[dict[str, Any]]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"scopes"}, body
    for definition in body["scopes"]:
        assert set(definition) == SCOPE_FIELDS, definition
    return body["scopes"]


def test_scopes_lists_the_definitions_a_default_token_is_granted(
    pats_client: PatsClient, mint_pat: MintPat
) -> None:
    names = [d["name"] for d in _scope_definitions(pats_client.scopes())]
    assert len(names) == len(set(names)), names

    # Both read the instance MCP scope set; the route drops names with no definition.
    default_scopes = mint_pat()["scopes"]
    assert set(names) <= set(default_scopes), (names, default_scopes)


def test_scopes_are_the_same_for_a_non_admin_member(
    pats_client: PatsClient, second_user: SecondUser
) -> None:
    member = _scope_definitions(request_as(second_user, "GET", "/scopes"))
    assert member == _scope_definitions(pats_client.scopes())


def test_scopes_rejects_a_call_without_a_token(pats_client: PatsClient) -> None:
    resp = pats_client.scopes(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_scopes_rejects_an_oauth_access_token(oauth_pats_client: PatsClient) -> None:
    resp = oauth_pats_client.scopes()
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert SESSION_ONLY in resp.text, resp.text[:500]


def test_scopes_rejects_a_personal_access_token(
    pats_client: PatsClient, mint_pat: MintPat
) -> None:
    resp = request_with_token(
        pats_client._client.base_url, mint_pat()["accessToken"], "GET", "/scopes"
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert SESSION_ONLY in resp.text, resp.text[:500]


def test_scopes_ignores_query_parameters(pats_client: PatsClient) -> None:
    expected = _scope_definitions(pats_client.scopes())

    with outside_request_contract("an undocumented query parameter; the handler reads no query"):
        resp = pats_client.scopes(params={"category": "Identity", "role": "member"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["scopes"] == expected


def test_scopes_rejects_a_service_token(service_token: str, pats_client: PatsClient) -> None:
    resp = request_with_token(pats_client._client.base_url, service_token, "GET", "/scopes")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert SESSION_ONLY in resp.text, resp.text[:500]
