"""Strict OpenAPI audit of POST /api/v1/personal-access-tokens."""

from __future__ import annotations

from typing import Any

import pytest
from personal_access_tokens_audit_support import UNKNOWN_SCOPE, PatsClient, pat_name
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/personal-access-tokens"

# PAT_TOKEN_PREFIX in oauth_provider/constants/constants.ts; the support module's PAT_PREFIX differs.
ACCESS_TOKEN_PREFIX = "phpat_"


def test_create_returns_token_with_one_time_secret(pats_client: PatsClient) -> None:
    name = pat_name()

    resp = pats_client.create({"name": name, "expiryDays": 90})

    token_id: str | None = None
    try:
        assert resp.status_code == 201, resp.text[:500]
        body = resp.json()
        token = body["token"]
        token_id = token["id"]
        assert body["message"] == "Personal access token created successfully"
        assert token["name"] == name
        assert token["scopes"], "omitted scopes should fall back to the instance MCP scope set"
        assert token["accessToken"].startswith(ACCESS_TOKEN_PREFIX)
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        if token_id is not None:
            pats_client.admin_revoke(token_id)


def test_create_without_token_is_unauthorized(pats_client: PatsClient) -> None:
    resp = pats_client.create({"name": pat_name()}, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_with_oauth_token_is_forbidden(oauth_pats_client: PatsClient) -> None:
    resp = oauth_pats_client.create({"name": pat_name()})

    # A 201 here would leave a live token behind; revoke it before failing.
    if resp.status_code == 201:
        oauth_pats_client.admin_revoke(resp.json()["token"]["id"])
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "overrides",
    [
        # zod: scopes is .min(1) when present.
        pytest.param({"scopes": []}, id="empty-scopes-fails-validation"),
        # Passes zod, then InvalidScopeError (400, code OAUTH_INVALID_SCOPE) in the service.
        pytest.param({"scopes": [UNKNOWN_SCOPE]}, id="unknown-scope"),
    ],
)
def test_create_rejects_bad_scopes(pats_client: PatsClient, overrides: dict[str, Any]) -> None:
    resp = pats_client.create({"name": pat_name(), **overrides})

    if resp.status_code == 201:
        pats_client.admin_revoke(resp.json()["token"]["id"])
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
