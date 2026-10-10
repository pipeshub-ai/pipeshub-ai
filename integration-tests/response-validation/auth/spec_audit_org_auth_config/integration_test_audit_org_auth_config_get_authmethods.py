"""Strict OpenAPI audit of GET /api/v1/orgAuthConfig/authMethods."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from helper.pipeshub_client import PipeshubClient
from org_auth_config_audit_support import (
    ACCOUNT_NOT_FOUND,
    ADMIN_ACCESS_REQUIRED,
    AUTH_METHODS_ROUTE,
    BAD_REQUEST,
    FORBIDDEN,
    INTERNAL_ERROR,
    MISSING_ORG_ID,
    NO_AUTHORIZATION_HEADER,
    NO_TOKEN_AFTER_SCHEME,
    NOT_FOUND,
    OrgAuthConfigClient,
    OTHER_SIGNING_KEY,
    mint_access_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import SignInPolicy, bearer, steps

pytestmark = pytest.mark.spec_audit

ROUTE = AUTH_METHODS_ROUTE


def _assert_error(resp: Any, status: int, code: str, message: str | None = None) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    if message is not None:
        assert error["message"] == message
    assert "authMethods" not in resp.text


def test_admin_reads_the_sign_in_policy(
    org_auth_config_client: OrgAuthConfigClient,
    admin_headers: dict[str, str],
    sign_in_policy: SignInPolicy,
) -> None:
    policy = steps(["password", "github"])
    with sign_in_policy.temporarily(policy):
        resp = org_auth_config_client.auth_methods(headers=admin_headers)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"authMethods": policy}


def test_unknown_query_parameters_are_ignored(
    org_auth_config_client: OrgAuthConfigClient, admin_headers: dict[str, str]
) -> None:
    with outside_request_contract("the route has no validator and never reads the query string"):
        resp = org_auth_config_client.auth_methods(
            headers=admin_headers, params={"specAuditUnknown": "1"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["authMethods"]


@pytest.mark.parametrize(
    ("authorization", "message"),
    [
        pytest.param(None, NO_AUTHORIZATION_HEADER, id="no_authorization_header"),
        pytest.param("Bearer", NO_TOKEN_AFTER_SCHEME, id="scheme_without_a_token"),
    ],
)
def test_request_without_a_bearer_token_is_bad_request(
    org_auth_config_client: OrgAuthConfigClient, authorization: str | None, message: str
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}
    _assert_error(
        org_auth_config_client.auth_methods(headers=headers), 400, BAD_REQUEST, message
    )


@pytest.mark.parametrize(
    "token_kind", ["not_a_jwt", "signed_with_another_key", "expired"]
)
def test_token_that_does_not_verify_is_an_internal_error(
    org_auth_config_client: OrgAuthConfigClient,
    access_token: Callable[..., str],
    admin_claims: dict[str, str],
    token_kind: str,
) -> None:
    # jwt.verify throws and nothing maps that to a 401.
    token = {
        "not_a_jwt": lambda: "spec-audit-not-a-jwt",
        "signed_with_another_key": lambda: mint_access_token(OTHER_SIGNING_KEY, **admin_claims),
        "expired": lambda: access_token(ttl_seconds=-60, **admin_claims),
    }[token_kind]()
    _assert_error(
        org_auth_config_client.auth_methods(headers=bearer(token)), 500, INTERNAL_ERROR
    )


@pytest.mark.parametrize("caller", ["member", "oauth_app", "admin_of_another_org"])
def test_caller_who_is_not_an_admin_of_the_org_is_forbidden(
    org_auth_config_client: OrgAuthConfigClient,
    pipeshub_client: PipeshubClient,
    member_headers: dict[str, str],
    access_token: Callable[..., str],
    admin_claims: dict[str, str],
    caller: str,
) -> None:
    if caller == "oauth_app":
        # The suite's client-credentials token verifies with the same key but names no admin.
        resp = pipeshub_client.request("GET", ROUTE)
    else:
        headers = {
            "member": lambda: member_headers,
            "admin_of_another_org": lambda: bearer(
                access_token(userId=admin_claims["userId"], orgId=MISSING_ORG_ID)
            ),
        }[caller]()
        resp = org_auth_config_client.auth_methods(headers=headers)
    _assert_error(resp, 403, FORBIDDEN, ADMIN_ACCESS_REQUIRED)


@pytest.mark.parametrize("missing", ["userId", "orgId"])
def test_token_without_user_or_org_is_not_found(
    org_auth_config_client: OrgAuthConfigClient,
    access_token: Callable[..., str],
    admin_claims: dict[str, str],
    missing: str,
) -> None:
    claims = {k: v for k, v in admin_claims.items() if k != missing}
    _assert_error(
        org_auth_config_client.auth_methods(headers=bearer(access_token(**claims))),
        404,
        NOT_FOUND,
        ACCOUNT_NOT_FOUND,
    )
