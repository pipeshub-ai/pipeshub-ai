"""Refusals every /api/v1/oauth-clients/:appId route shares, checked strictly route by route.

authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller ->
appIdParamsSchema (or updateAppSchema / setAppTokenIdentitySchema) -> creator-scoped lookup.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from oauth_clients_audit_support import (
    ACTIVATE_ROUTE,
    APP_ROUTE,
    MALFORMED_APP_ID,
    MISSING_APP_ID,
    REGENERATE_SECRET_ROUTE,
    REVOKE_ALL_TOKENS_ROUTE,
    SUSPEND_ROUTE,
    TOKEN_IDENTITY_ROUTE,
    TOKENS_ROUTE,
    OAuthClientsAuditClient,
    SeedOAuthApp,
    request_as,
    token_identity_body,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

# (method, suffix after /:appId, route, JSON body or None)
APP_OPERATIONS: list[tuple[str, str, str, dict[str, Any] | None]] = [
    ("GET", "", APP_ROUTE, None),
    ("PUT", "", APP_ROUTE, {"name": "spec-audit renamed"}),
    ("DELETE", "", APP_ROUTE, None),
    ("POST", "/regenerate-secret", REGENERATE_SECRET_ROUTE, None),
    ("POST", "/suspend", SUSPEND_ROUTE, None),
    ("POST", "/activate", ACTIVATE_ROUTE, None),
    ("GET", "/tokens", TOKENS_ROUTE, None),
    ("POST", "/revoke-all-tokens", REVOKE_ALL_TOKENS_ROUTE, None),
    ("PUT", "/token-identity", TOKEN_IDENTITY_ROUTE, token_identity_body(None)),
]
APP_OPERATION_IDS = [
    "get",
    "update",
    "delete",
    "regenerate_secret",
    "suspend",
    "activate",
    "tokens",
    "revoke_all_tokens",
    "token_identity",
]
# token-identity is admin-only, so a member is refused 403 there before the lookup.
CREATOR_SCOPED = [op for op in APP_OPERATIONS if op[2] != TOKEN_IDENTITY_ROUTE]
CREATOR_SCOPED_IDS = [i for i in APP_OPERATION_IDS if i != "token_identity"]


def _call(
    client: OAuthClientsAuditClient, method: str, app_id: str, suffix: str, body: Any, **kwargs: Any
):
    if body is not None:
        kwargs["json"] = body
    return client._client.request(method, f"{client.BASE}/{app_id}{suffix}", **kwargs)


@pytest.mark.parametrize(("method", "suffix", "route", "body"), APP_OPERATIONS, ids=APP_OPERATION_IDS)
def test_without_token_is_unauthorized(
    oauth_clients_client: OAuthClientsAuditClient, method: str, suffix: str, route: str, body: Any
) -> None:
    resp = _call(oauth_clients_client, method, MISSING_APP_ID, suffix, body, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, route)


@pytest.mark.parametrize(("method", "suffix", "route", "body"), APP_OPERATIONS, ids=APP_OPERATION_IDS)
def test_with_oauth_access_token_is_forbidden(
    oauth_token_clients_client: OAuthClientsAuditClient, method: str, suffix: str, route: str, body: Any
) -> None:
    resp = _call(oauth_token_clients_client, method, MISSING_APP_ID, suffix, body)

    assert resp.status_code == 403, resp.text[:500]
    assert "interactive user session" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, route)


@pytest.mark.parametrize(("method", "suffix", "route", "body"), APP_OPERATIONS, ids=APP_OPERATION_IDS)
def test_malformed_app_id_is_a_validation_error(
    oauth_clients_client: OAuthClientsAuditClient, method: str, suffix: str, route: str, body: Any
) -> None:
    resp = _call(oauth_clients_client, method, MALFORMED_APP_ID, suffix, body)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == ["params.appId"]
    assert_strict_openapi_exchange(resp, route)


@pytest.mark.parametrize(("method", "suffix", "route", "body"), APP_OPERATIONS, ids=APP_OPERATION_IDS)
def test_unknown_app_is_not_found(
    oauth_clients_client: OAuthClientsAuditClient, method: str, suffix: str, route: str, body: Any
) -> None:
    resp = _call(oauth_clients_client, method, MISSING_APP_ID, suffix, body)

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "OAuth app not found"
    assert_strict_openapi_exchange(resp, route)


@pytest.mark.parametrize(("method", "suffix", "route", "body"), CREATOR_SCOPED, ids=CREATOR_SCOPED_IDS)
def test_another_members_app_is_not_found(
    seed_oauth_app: SeedOAuthApp,
    second_user: SecondUser,
    method: str,
    suffix: str,
    route: str,
    body: Any,
) -> None:
    # Creator-scoped: the admin's app does not exist for any other member.
    app = seed_oauth_app()
    kwargs: dict[str, Any] = {} if body is None else {"json": body}

    resp = request_as(second_user, method, f"/{app['id']}{suffix}", **kwargs)

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "OAuth app not found"
    assert_strict_openapi_exchange(resp, route)
