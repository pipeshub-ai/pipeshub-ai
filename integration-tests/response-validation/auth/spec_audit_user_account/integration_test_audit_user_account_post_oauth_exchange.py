"""Strict OpenAPI audit of POST /api/v1/userAccount/oauth/exchange.

The org's generic OAuth sign-in points at a local provider stub, so every answer the
route gives after talking to a provider can be produced on purpose.
"""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from user_account_audit_support import (
    BAD_REQUEST,
    OAUTH_EXCHANGE_ROUTE,
    OAUTH_SETTING,
    OAUTH_SIGN_IN_FAILED,
    UNAUTHORIZED,
    Account,
    OAuthProviderStub,
    UserAccountAuditClient,
    oauth_exchange_body,
    without_setting,
)

pytestmark = pytest.mark.spec_audit

ROUTE = OAUTH_EXCHANGE_ROUTE
# Messages in userAccount.controller.ts.
NOT_SET_UP = (
    "Single sign-on isn't fully set up yet. Ask your admin to finish the sign-in settings, "
    "or use another sign-in method."
)
PROVIDER_SHARED_NO_EMAIL = (
    "Your sign-in provider didn't share an email address, so we couldn't sign you in. "
    "Ask your admin to allow the email permission for PipesHub."
)
ACCOUNT_NOT_FOUND = "Account not found. Please contact your administrator."
TOKEN_FIELDS = {"access_token", "id_token", "token_type", "expires_in"}


def _assert_refused(resp: Any, status: int, code: str, message: str) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    assert error["message"] == message
    assert "access_token" not in resp.json()


def test_code_for_an_account_returns_the_provider_tokens(
    user_account_audit_client: UserAccountAuditClient,
    oauth_provider: OAuthProviderStub,
    module_account: Account,
) -> None:
    code, access_token = oauth_provider.issue(module_account.email)

    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body(code=code))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    answer = resp.json()
    assert set(answer) == TOKEN_FIELDS
    assert answer["access_token"] == access_token
    assert answer["token_type"] == "Bearer"
    assert answer["expires_in"] == 3600

    sent = oauth_provider.token_requests[-1]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == code
    assert sent["redirect_uri"] == oauth_exchange_body()["redirectUri"]


def test_fields_the_provider_leaves_out_are_absent(
    user_account_audit_client: UserAccountAuditClient,
    oauth_provider: OAuthProviderStub,
    module_account: Account,
) -> None:
    code, access_token = oauth_provider.issue(module_account.email)
    oauth_provider.codes[code] = (200, {"access_token": access_token})

    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body(code=code))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"access_token": access_token}


def test_provider_is_not_read_and_unknown_fields_are_ignored(
    user_account_audit_client: UserAccountAuditClient,
    oauth_provider: OAuthProviderStub,
    module_account: Account,
) -> None:
    code, _ = oauth_provider.issue(module_account.email)
    with outside_request_contract("no validator: provider only has to be truthy, other fields and the query are not read"):
        resp = user_account_audit_client.oauth_exchange(
            oauth_exchange_body(code=code, provider=5, specAuditUnknown=True),
            params={"specAuditUnknown": "1"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"code": None}, id="no_code"),
        pytest.param({"provider": None}, id="no_provider"),
        pytest.param({"redirectUri": None}, id="no_redirect_uri"),
        pytest.param({"code": ""}, id="empty_code"),
        pytest.param({"provider": ""}, id="empty_provider"),
        pytest.param({"redirectUri": ""}, id="empty_redirect_uri"),
    ],
)
def test_missing_or_empty_field_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, overrides: dict[str, Any]
) -> None:
    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body(**overrides))
    _assert_refused(resp, 400, BAD_REQUEST, OAUTH_SIGN_IN_FAILED)
    # No validator: the handler's own truthiness check is what refuses these.
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize("body", [pytest.param(None, id="no_body"), pytest.param(["x"], id="array_body")])
def test_no_json_object_body_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, body: Any
) -> None:
    resp = user_account_audit_client.oauth_exchange(body)
    _assert_refused(resp, 400, BAD_REQUEST, OAUTH_SIGN_IN_FAILED)
    assert_spec_forbids_request(resp, ROUTE)


def test_code_the_provider_rejects_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, oauth_provider: OAuthProviderStub
) -> None:
    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body(code="spec-audit-never-issued"))
    _assert_refused(resp, 400, BAD_REQUEST, OAUTH_SIGN_IN_FAILED)


def test_access_token_the_userinfo_endpoint_rejects_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    oauth_provider: OAuthProviderStub,
    module_account: Account,
) -> None:
    code, access_token = oauth_provider.issue(module_account.email)
    del oauth_provider.access_tokens[access_token]

    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body(code=code))
    _assert_refused(resp, 401, UNAUTHORIZED, OAUTH_SIGN_IN_FAILED)


def test_provider_that_shares_no_email_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, oauth_provider: OAuthProviderStub
) -> None:
    code, _ = oauth_provider.issue(None)

    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body(code=code))
    _assert_refused(resp, 400, BAD_REQUEST, PROVIDER_SHARED_NO_EMAIL)


def test_email_with_no_account_is_not_found_while_jit_is_off(
    user_account_audit_client: UserAccountAuditClient,
    oauth_provider: OAuthProviderStub,
    stray_email: str,
) -> None:
    code, _ = oauth_provider.issue(stray_email)

    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body(code=code))
    _assert_refused(resp, 404, "HTTP_NOT_FOUND", ACCOUNT_NOT_FOUND)


def test_exchange_without_saved_oauth_settings_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, oauth_provider: OAuthProviderStub
) -> None:
    with without_setting(OAUTH_SETTING):
        resp = user_account_audit_client.oauth_exchange(oauth_exchange_body())
    _assert_refused(resp, 400, BAD_REQUEST, NOT_SET_UP)
