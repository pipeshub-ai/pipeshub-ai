"""Strict OpenAPI audit of POST /api/v1/orgAuthConfig.

This deployment already has its organisation, so only the "already done" answer is
reachable; the first-run 201 would create a second organisation.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
from helper.pipeshub_client import PipeshubClient
from org_auth_config_audit_support import (
    ACCOUNT_NOT_FOUND,
    ADMIN_ACCESS_REQUIRED,
    ALREADY_DONE,
    BAD_REQUEST,
    FORBIDDEN,
    INTERNAL_ERROR,
    MISSING_ORG_ID,
    NO_AUTHORIZATION_HEADER,
    NO_TOKEN_AFTER_SCHEME,
    NOT_FOUND,
    SET_UP_ROUTE,
    OrgAuthConfigClient,
    OTHER_SIGNING_KEY,
    mint_access_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import bearer

pytestmark = pytest.mark.spec_audit

ROUTE = SET_UP_ROUTE
SETUP_BODY = {
    "contactEmail": "spec-audit-org@test-pipeshub.com",
    "registeredName": "Spec Audit Org",
    "adminFullName": "Spec Audit Admin",
    "sendEmail": False,
}


def _assert_already_done(resp: Any) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == ALREADY_DONE


def _assert_error(resp: Any, status: int, code: str, message: str | None = None) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    if message is not None:
        assert error["message"] == message


def test_set_up_on_a_configured_deployment_is_already_done(
    org_auth_config_client: OrgAuthConfigClient, admin_headers: dict[str, str]
) -> None:
    _assert_already_done(org_auth_config_client.set_up(headers=admin_headers, json=SETUP_BODY))


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="no_body"),
        pytest.param({"json": {}}, id="empty_object"),
        pytest.param({"json": {"contactEmail": 5}}, id="wrong_type"),
        pytest.param({"json": {**SETUP_BODY, "specAuditUnknown": True}}, id="unknown_field"),
        pytest.param({"json": SETUP_BODY, "params": {"specAuditUnknown": "1"}}, id="unknown_query"),
    ],
)
def test_body_is_not_read_once_the_org_exists(
    org_auth_config_client: OrgAuthConfigClient, admin_headers: dict[str, str], kwargs: dict[str, Any]
) -> None:
    with outside_request_contract("no validator; the existing-org answer comes before the body is read"):
        _assert_already_done(org_auth_config_client.set_up(headers=admin_headers, **kwargs))


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
        org_auth_config_client.set_up(headers=headers, json=SETUP_BODY), 400, BAD_REQUEST, message
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
    token = {
        "not_a_jwt": lambda: "spec-audit-not-a-jwt",
        "signed_with_another_key": lambda: mint_access_token(OTHER_SIGNING_KEY, **admin_claims),
        "expired": lambda: access_token(ttl_seconds=-60, **admin_claims),
    }[token_kind]()
    _assert_error(
        org_auth_config_client.set_up(headers=bearer(token), json=SETUP_BODY), 500, INTERNAL_ERROR
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
        resp = pipeshub_client.request("POST", ROUTE, json=SETUP_BODY)
    else:
        headers = {
            "member": lambda: member_headers,
            "admin_of_another_org": lambda: bearer(
                access_token(userId=admin_claims["userId"], orgId=MISSING_ORG_ID)
            ),
        }[caller]()
        resp = org_auth_config_client.set_up(headers=headers, json=SETUP_BODY)
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
        org_auth_config_client.set_up(headers=bearer(access_token(**claims)), json=SETUP_BODY),
        404,
        NOT_FOUND,
        ACCOUNT_NOT_FOUND,
    )
