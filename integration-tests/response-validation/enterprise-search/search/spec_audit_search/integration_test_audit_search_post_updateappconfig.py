"""Strict OpenAPI audit of POST /api/v1/search/updateAppConfig."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from search_audit_support import (
    FETCH_CONFIG_SCOPE,
    OTHER_SCOPE,
    UPDATE_APP_CONFIG_MESSAGE,
    WRONG_SECRET,
    MintScopedToken,
    SearchAuditClient,
    mint_scoped_token,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/search/updateAppConfig"


def test_update_app_config_with_fetch_config_token_reloads_config(
    search_audit_client: SearchAuditClient, scoped_token: MintScopedToken
) -> None:
    resp = search_audit_client.update_app_config(token=scoped_token(FETCH_CONFIG_SCOPE))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": UPDATE_APP_CONFIG_MESSAGE}, resp.text[:500]


def test_update_app_config_ignores_a_body(
    search_audit_client: SearchAuditClient, scoped_token: MintScopedToken
) -> None:
    with outside_request_contract("the route takes no body; one that is sent is ignored, not refused"):
        resp = search_audit_client.update_app_config(
            token=scoped_token(FETCH_CONFIG_SCOPE), json={"ignored": True}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": UPDATE_APP_CONFIG_MESSAGE}, resp.text[:500]


def test_update_app_config_without_token_is_401(
    search_audit_client: SearchAuditClient,
) -> None:
    resp = search_audit_client.update_app_config(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "No token provided" in resp.text, resp.text[:500]


def test_update_app_config_rejects_member_session_token(
    second_user: SecondUser,
) -> None:
    # Session tokens are signed with the session secret, so the scoped-token check fails before any scope test.
    resp = request_as(second_user, "POST", "/updateAppConfig")
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Invalid token" in resp.text, resp.text[:500]


def test_update_app_config_rejects_token_signed_with_another_secret(
    search_audit_client: SearchAuditClient,
) -> None:
    resp = search_audit_client.update_app_config(
        token=mint_scoped_token(WRONG_SECRET, [FETCH_CONFIG_SCOPE])
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Invalid token" in resp.text, resp.text[:500]


def test_update_app_config_with_another_scope_is_401_not_403(
    search_audit_client: SearchAuditClient, scoped_token: MintScopedToken
) -> None:
    resp = search_audit_client.update_app_config(token=scoped_token(OTHER_SCOPE))
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Invalid scope" in resp.text, resp.text[:500]
