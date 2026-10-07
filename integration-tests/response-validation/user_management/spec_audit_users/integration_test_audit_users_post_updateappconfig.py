"""Strict OpenAPI audit of POST /api/v1/users/updateAppConfig."""

from __future__ import annotations

import pytest
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    FETCH_CONFIG_SCOPE,
    INVALID_BEARER,
    OTHER_SCOPE,
    USER_LOOKUP_SCOPE,
    WRONG_SECRET,
    MintScopedToken,
    mint_scoped_token,
    request_with_token,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/updateAppConfig"
PATH = "/updateAppConfig"
RELOADED = {"message": "User configuration updated successfully"}


def test_update_app_config_with_fetch_config_token_reloads_config(
    pipeshub_client: PipeshubClient,
    scoped_token: MintScopedToken,
) -> None:
    # Reloads the stored config and rebinds DI services: idempotent.
    resp = request_with_token(
        pipeshub_client, scoped_token(FETCH_CONFIG_SCOPE), "POST", PATH
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == RELOADED


def test_a_body_and_query_are_ignored(
    pipeshub_client: PipeshubClient, scoped_token: MintScopedToken
) -> None:
    with outside_request_contract("the route reads no body or query; this shows both are ignored"):
        resp = request_with_token(
            pipeshub_client,
            scoped_token(FETCH_CONFIG_SCOPE),
            "POST",
            PATH,
            params={"force": "true"},
            json={"smtp": {"host": "spec-audit.invalid"}},
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == RELOADED


@pytest.mark.parametrize(
    ("scopes", "ttl"),
    [([OTHER_SCOPE], 3600), ([USER_LOOKUP_SCOPE], 3600), ([], 3600), ([FETCH_CONFIG_SCOPE], -60)],
    ids=["other-scope", "user-lookup-scope", "no-scope", "expired"],
)
def test_a_token_without_a_live_fetch_config_scope_is_unauthorized(
    pipeshub_client: PipeshubClient, scoped_token: MintScopedToken, scopes: list[str], ttl: int
) -> None:
    resp = request_with_token(pipeshub_client, scoped_token(*scopes, ttl_seconds=ttl), "POST", PATH)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_app_config_with_wrongly_signed_token_is_unauthorized(
    pipeshub_client: PipeshubClient,
) -> None:
    token = mint_scoped_token(
        WRONG_SECRET,
        [FETCH_CONFIG_SCOPE],
        userId=pipeshub_client.acting_user_id,
        orgId=pipeshub_client.org_id,
    )
    resp = request_with_token(pipeshub_client, token, "POST", PATH)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_app_config_with_admin_token_is_unauthorized(
    users_client: UsersClient,
) -> None:
    # The route takes scoped service tokens only; being an org admin does not help.
    resp = users_client.post(PATH)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.post(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
