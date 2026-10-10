"""Strict OpenAPI audit of GET /api/v1/oauth-clients/scopes.

authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller ->
listScopes (no validator; the scope set depends on whether the caller is an org admin).
"""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from oauth_clients_audit_support import (
    ADMIN_ONLY_SCOPES,
    SCOPES_ROUTE,
    OAuthClientsAuditClient,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = SCOPES_ROUTE


def _scope_names(body: dict) -> set[str]:
    names = set()
    for category, scopes in body["scopes"].items():
        for scope in scopes:
            assert scope["category"] == category
            names.add(scope["name"])
    return names


def test_admin_receives_every_scope_grouped_by_category(
    oauth_clients_client: OAuthClientsAuditClient,
) -> None:
    resp = oauth_clients_client.list_scopes()

    assert resp.status_code == 200, resp.text[:500]
    names = _scope_names(resp.json())
    assert {"openid", "profile", *ADMIN_ONLY_SCOPES} <= names
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_receives_no_admin_only_scope(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/scopes")

    assert resp.status_code == 200, resp.text[:500]
    names = _scope_names(resp.json())
    assert "openid" in names
    assert names.isdisjoint(ADMIN_ONLY_SCOPES)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_query_parameters_are_ignored(oauth_clients_client: OAuthClientsAuditClient) -> None:
    with outside_request_contract("an undocumented query parameter on a route that reads none"):
        resp = oauth_clients_client.list_scopes(params={"category": "Identity"})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.json()["scopes"]) > 1


def test_scopes_without_token_is_unauthorized(oauth_clients_client: OAuthClientsAuditClient) -> None:
    resp = oauth_clients_client.list_scopes(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_scopes_with_oauth_access_token_is_forbidden(
    oauth_token_clients_client: OAuthClientsAuditClient,
) -> None:
    resp = oauth_token_clients_client.list_scopes()

    assert resp.status_code == 403, resp.text[:500]
    assert "interactive user session" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
