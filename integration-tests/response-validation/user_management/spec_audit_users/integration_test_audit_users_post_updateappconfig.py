"""Strict OpenAPI audit of POST /api/v1/users/updateAppConfig."""

from __future__ import annotations

import pytest
from users_audit_support import (
    FETCH_CONFIG_SCOPE,
    OTHER_SCOPE,
    WRONG_SECRET,
    MintScopedToken,
    mint_scoped_token,
    request_with_token,
)
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/updateAppConfig"
PATH = "/updateAppConfig"


def test_update_app_config_with_fetch_config_token_reloads_config(
    pipeshub_client: PipeshubClient,
    scoped_token: MintScopedToken,
) -> None:
    # Reloads the stored config and rebinds DI services: idempotent, but only
    # this one case may reach the handler.
    resp = request_with_token(
        pipeshub_client, scoped_token(FETCH_CONFIG_SCOPE), "POST", PATH
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "User configuration updated successfully"}


def test_update_app_config_with_other_scope_is_unauthorized(
    pipeshub_client: PipeshubClient,
    scoped_token: MintScopedToken,
) -> None:
    resp = request_with_token(pipeshub_client, scoped_token(OTHER_SCOPE), "POST", PATH)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


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
    assert_strict_openapi_response(resp, ROUTE)


def test_update_app_config_with_admin_session_token_is_unauthorized(
    users_client: UsersClient,
) -> None:
    # The route takes scoped service tokens only; being an org admin does not help.
    resp = users_client.post(PATH)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_app_config_without_token_is_unauthorized(
    users_client: UsersClient,
) -> None:
    resp = users_client.post(PATH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
