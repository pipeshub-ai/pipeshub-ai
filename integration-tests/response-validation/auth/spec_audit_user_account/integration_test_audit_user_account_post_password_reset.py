"""Strict OpenAPI audit of POST /api/v1/userAccount/password/reset."""

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
    INTERNAL_ERROR,
    NEW_PASSWORD,
    NO_AUTHORIZATION_HEADER,
    PASSWORD_RESET_ROUTE,
    UNAUTHORIZED,
    VALIDATION_ERROR,
    WEAK_PASSWORD,
    WRONG_EMAIL_OR_PASSWORD,
    Account,
    Tokens,
    UserAccountAuditClient,
    delete_credentials,
    lock_account,
    password_body,
)

pytestmark = pytest.mark.spec_audit

ROUTE = PASSWORD_RESET_ROUTE
PASSWORD_POLICY = (
    "Password should have minimum 8 characters with at least one uppercase, one lowercase, "
    "one number, and one special character, and be no longer than 72 bytes."
)


def _change(account: Account, **overrides: Any) -> dict[str, Any]:
    body = {"currentPassword": account.password, "newPassword": NEW_PASSWORD, **overrides}
    return {k: v for k, v in body.items() if v is not None}


def _assert_refused(resp: Any, status: int, code: str, message: str | None) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    if message is not None:
        assert error["message"] == message


def test_password_is_changed_and_a_new_access_token_returned(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)

    resp = user_account_audit_client.password_reset(_change(account), token=tokens.access)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    answer = resp.json()
    assert answer["data"] == "password reset"
    assert answer["accessToken"].count(".") == 2

    user_account_audit_client.sign_in(account, NEW_PASSWORD)
    old = user_account_audit_client.authenticate(
        password_body(account.password, account.email),
        session=user_account_audit_client.start_session(),
    )
    assert old.status_code == 400, old.text[:500]
    assert old.json()["error"]["message"] == WRONG_EMAIL_OR_PASSWORD


def test_turnstile_token_is_accepted_when_captcha_is_off(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    body = {**_change(account), "cf-turnstile-response": "spec-audit-token"}

    resp = user_account_audit_client.password_reset(body, token=tokens.access)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_fields_and_the_query_string_are_ignored(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)

    with outside_request_contract("the validator drops body fields it does not know and never looks at the query"):
        resp = user_account_audit_client.password_reset(
            {**_change(account), "specAuditUnknown": True},
            token=tokens.access,
            params={"specAuditUnknown": "1"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.newPassword", id="empty_object"),
        pytest.param(None, "body.newPassword", id="no_body"),
        pytest.param({"currentPassword": "x"}, "body.newPassword", id="no_new_password"),
        pytest.param(
            {"currentPassword": "x", "newPassword": 12345678}, "body.newPassword", id="numeric_new_password"
        ),
        pytest.param(
            {"currentPassword": 1, "newPassword": NEW_PASSWORD},
            "body.currentPassword",
            id="numeric_current_password",
        ),
        pytest.param(
            {"currentPassword": "x", "newPassword": NEW_PASSWORD, "cf-turnstile-response": 1},
            "body.cf-turnstile-response",
            id="numeric_turnstile_token",
        ),
        pytest.param(["not", "an", "object"], "body", id="array_body"),
    ],
)
def test_password_reset_refuses_what_the_validator_refuses(
    user_account_audit_client: UserAccountAuditClient, module_tokens: Tokens, body: Any, field: str
) -> None:
    resp = user_account_audit_client.password_reset(body, token=module_tokens.access)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == VALIDATION_ERROR
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"currentPassword": None}, "currentPassword is required", id="no_current_password"),
        pytest.param({"currentPassword": ""}, "currentPassword is required", id="empty_current_password"),
        pytest.param({"newPassword": ""}, "newPassword is required", id="empty_new_password"),
    ],
)
def test_password_the_handler_requires_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    module_tokens: Tokens,
    overrides: dict[str, Any],
    message: str,
) -> None:
    resp = user_account_audit_client.password_reset(
        _change(module_account, **overrides), token=module_tokens.access
    )
    _assert_refused(resp, 400, BAD_REQUEST, message)
    # The validator lets these through; the handler's own check is what requires them.
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("new_password", "message"),
    [
        pytest.param(None, "Current and new password cannot be same", id="same_as_current"),
        pytest.param(WEAK_PASSWORD, PASSWORD_POLICY, id="too_short"),
        pytest.param("onlylowercaseletters", PASSWORD_POLICY, id="no_required_mix"),
    ],
)
def test_new_password_the_handler_refuses_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    module_tokens: Tokens,
    new_password: str | None,
    message: str,
) -> None:
    resp = user_account_audit_client.password_reset(
        _change(module_account, newPassword=new_password or module_account.password),
        token=module_tokens.access,
    )
    _assert_refused(resp, 400, BAD_REQUEST, message)


def test_wrong_current_password_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient, module_account: Account, module_tokens: Tokens
) -> None:
    resp = user_account_audit_client.password_reset(
        _change(module_account, currentPassword="SpecAudit#Wrong999"), token=module_tokens.access
    )
    _assert_refused(resp, 401, UNAUTHORIZED, "Current password is incorrect.")


def test_locked_account_cannot_change_its_password(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    lock_account(account.org_id, account.user_id)

    resp = user_account_audit_client.password_reset(_change(account), token=tokens.access)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"].startswith(
        "You cannot change you password as your account is blocked due to multiple incorrect logins"
    )


def test_account_with_no_stored_password_is_not_found(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    delete_credentials(account.org_id, account.user_id)

    resp = user_account_audit_client.password_reset(_change(account), token=tokens.access)
    _assert_refused(resp, 404, "HTTP_NOT_FOUND", "Previous password not found")


@pytest.mark.parametrize(
    ("authorization", "message"),
    [
        pytest.param(None, NO_AUTHORIZATION_HEADER, id="no_authorization_header"),
        pytest.param("Bearer", "Token not found in Authorization header", id="scheme_without_a_token"),
    ],
)
def test_request_without_a_bearer_token_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    authorization: str | None,
    message: str,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    resp = user_account_audit_client.password_reset(_change(module_account), headers=headers)
    _assert_refused(resp, 400, BAD_REQUEST, message)


@pytest.mark.parametrize("token_kind", ["not_a_jwt", "refresh_token"])
def test_token_that_does_not_verify_is_an_internal_error(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    module_tokens: Tokens,
    token_kind: str,
) -> None:
    # jwt.verify throws and nothing maps that to a 401.
    token = "spec-audit-not-a-jwt" if token_kind == "not_a_jwt" else module_tokens.refresh

    resp = user_account_audit_client.password_reset(_change(module_account), token=token)
    _assert_refused(resp, 500, INTERNAL_ERROR, None)
