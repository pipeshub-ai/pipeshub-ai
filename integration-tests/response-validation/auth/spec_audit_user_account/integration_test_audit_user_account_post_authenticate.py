"""Strict OpenAPI audit of POST /api/v1/userAccount/authenticate."""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import (
    ACCOUNT_PASSWORD,
    AUTHENTICATE_ROUTE,
    BAD_REQUEST,
    INTERNAL_ERROR,
    OAUTH_SIGN_IN_FAILED,
    SIGN_IN_METHOD_NOT_ALLOWED,
    UNAUTHORIZED,
    VALIDATION_ERROR,
    WRONG_EMAIL_OR_PASSWORD,
    WRONG_SIGN_IN_CODE,
    Account,
    OAuthProviderStub,
    SignInPolicy,
    UserAccountAuditClient,
    emailed_sign_in_code,
    lock_account,
    otp_body,
    password_body,
    steps,
)

pytestmark = pytest.mark.spec_audit

ROUTE = AUTHENTICATE_ROUTE

FULLY_AUTHENTICATED = {"message", "accessToken", "refreshToken"}
# Messages in userAccount.controller.ts.
SAML_HAS_ITS_OWN_SIGN_IN = (
    "Single sign-on (SAML) can't be completed with this request. On the sign-in page, "
    "choose your organisation's single sign-on option. Apps calling the API directly "
    "should send the browser to /api/v1/saml/signIn instead."
)
PROVIDER_SHARED_NO_EMAIL = (
    "Your sign-in provider didn't share an email address, so we couldn't sign you in. "
    "Ask your admin to allow the email permission for PipesHub."
)
ACCOUNT_NOT_FOUND = "Account not found. Please contact your administrator."
PASSWORD_AND_OTP = steps(["password", "otp"])
PASSWORD_AND_OAUTH = steps(["password", "oauth"])


def _assert_signed_in(resp: Any) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    answer = resp.json()
    assert set(answer) == FULLY_AUTHENTICATED
    assert answer["message"] == "Fully authenticated"


def _assert_refused(resp: Any, status: int, code: str, message: str) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    assert error["message"] == message
    assert "accessToken" not in resp.text


@pytest.mark.parametrize("email_in", ["body", "session"])
def test_password_sign_in_returns_tokens(
    user_account_audit_client: UserAccountAuditClient, module_account: Account, email_in: str
) -> None:
    if email_in == "body":
        session = user_account_audit_client.start_session()
        body = password_body(module_account.password, module_account.email)
    else:
        session = user_account_audit_client.start_session(module_account.email)
        body = password_body(module_account.password)

    _assert_signed_in(user_account_audit_client.authenticate(body, session=session))


def test_sign_in_with_no_email_anywhere_is_an_internal_error(
    user_account_audit_client: UserAccountAuditClient,
) -> None:
    # The account lookup is made with an empty email, the users service refuses
    # it, and that refusal is reported as an unknown error.
    resp = user_account_audit_client.authenticate(
        password_body(ACCOUNT_PASSWORD), session=user_account_audit_client.start_session()
    )
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == INTERNAL_ERROR


@pytest.mark.parametrize(
    "credentials",
    [
        pytest.param({"password": "SpecAudit#Wrong999"}, id="wrong_password"),
        pytest.param("SpecAudit#Pass123", id="password_as_bare_string"),
        pytest.param({}, id="no_password"),
    ],
)
def test_wrong_or_missing_password_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, module_account: Account, credentials: Any
) -> None:
    resp = user_account_audit_client.authenticate(
        {"method": "password", "credentials": credentials, "email": module_account.email},
        session=user_account_audit_client.start_session(),
    )
    _assert_refused(resp, 400, BAD_REQUEST, WRONG_EMAIL_OR_PASSWORD)


def test_email_with_no_account_gets_the_wrong_password_answer(
    user_account_audit_client: UserAccountAuditClient, stray_email: str
) -> None:
    resp = user_account_audit_client.authenticate(
        password_body(ACCOUNT_PASSWORD, stray_email),
        session=user_account_audit_client.start_session(),
    )
    _assert_refused(resp, 400, BAD_REQUEST, WRONG_EMAIL_OR_PASSWORD)


def test_locked_account_gets_the_wrong_password_answer_for_the_right_password(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    lock_account(account.org_id, account.user_id)

    resp = user_account_audit_client.authenticate(
        password_body(account.password, account.email),
        session=user_account_audit_client.start_session(),
    )
    _assert_refused(resp, 400, BAD_REQUEST, WRONG_EMAIL_OR_PASSWORD)


@pytest.mark.parametrize(
    ("session", "message"),
    [
        pytest.param(None, "Invalid session token", id="no_session_header"),
        pytest.param("spec-audit-not-a-session", "Invalid session", id="unknown_session"),
    ],
)
def test_missing_or_unknown_session_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    session: str | None,
    message: str,
) -> None:
    resp = user_account_audit_client.authenticate(
        password_body(module_account.password, module_account.email), session=session
    )
    _assert_refused(resp, 401, UNAUTHORIZED, message)


def test_session_is_checked_before_the_body(
    user_account_audit_client: UserAccountAuditClient,
) -> None:
    with outside_request_contract("an empty body, to show the session check runs before the validator"):
        resp = user_account_audit_client.authenticate({})
        _assert_refused(resp, 401, UNAUTHORIZED, "Invalid session token")


def test_session_ends_with_the_sign_in_it_completed(
    user_account_audit_client: UserAccountAuditClient, module_account: Account
) -> None:
    session = user_account_audit_client.start_session()
    body = password_body(module_account.password, module_account.email)
    _assert_signed_in(user_account_audit_client.authenticate(body, session=session))

    _assert_refused(
        user_account_audit_client.authenticate(body, session=session),
        401,
        UNAUTHORIZED,
        "Invalid session",
    )


@pytest.mark.parametrize(
    ("method", "credentials"),
    [
        pytest.param("otp", {"otp": "123456"}, id="otp"),
        pytest.param("samlSso", "spec-audit", id="samlSso"),
        pytest.param("oauth", {"accessToken": "spec-audit"}, id="oauth"),
        pytest.param("spec-audit-no-such-method", "spec-audit", id="unknown_method"),
    ],
)
def test_method_outside_the_session_step_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    module_account: Account,
    method: str,
    credentials: Any,
) -> None:
    session = sign_in_policy.session_under(steps(["password"]), user_account_audit_client)

    resp = user_account_audit_client.authenticate(
        {"method": method, "credentials": credentials, "email": module_account.email},
        session=session,
    )
    _assert_refused(resp, 400, BAD_REQUEST, SIGN_IN_METHOD_NOT_ALLOWED)


@pytest.mark.parametrize(
    ("method", "message"),
    [
        pytest.param("samlSso", SAML_HAS_ITS_OWN_SIGN_IN, id="samlSso"),
        pytest.param("github", "Unsupported authentication method", id="github"),
    ],
)
def test_allowed_method_with_no_flow_here_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    module_account: Account,
    method: str,
    message: str,
) -> None:
    session = sign_in_policy.session_under(
        steps(["password", method]), user_account_audit_client
    )

    resp = user_account_audit_client.authenticate(
        {"method": method, "credentials": "spec-audit", "email": module_account.email},
        session=session,
    )
    _assert_refused(resp, 400, BAD_REQUEST, message)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"credentials": {"password": "x"}}, "body.method", id="no_method"),
        pytest.param({"method": "", "credentials": "x"}, "body.method", id="empty_method"),
        pytest.param({"method": 7, "credentials": "x"}, "body.method", id="numeric_method"),
        pytest.param({"method": "password"}, "body.credentials", id="no_credentials"),
        pytest.param({"method": "password", "credentials": ""}, "body.credentials", id="empty_credentials"),
        pytest.param({"method": "password", "credentials": 5}, "body.credentials", id="numeric_credentials"),
        pytest.param(
            {"method": "password", "credentials": {"password": 5}},
            "body.credentials",
            id="numeric_password",
        ),
        pytest.param(
            {"method": "otp", "credentials": {"otp": 123456}}, "body.credentials", id="numeric_otp"
        ),
        pytest.param(
            {"method": "password", "credentials": "x", "email": "not-an-email"},
            "body.email",
            id="malformed_email",
        ),
        pytest.param(
            {"method": "password", "credentials": "x", "email": "a" * 250 + "@example.com"},
            "body.email",
            id="email_over_254_chars",
        ),
        pytest.param(
            {"method": "password", "credentials": "x", "cf-turnstile-response": 1},
            "body.cf-turnstile-response",
            id="numeric_turnstile_token",
        ),
        pytest.param(
            {"method": "password", "credentials": "x", "specAuditUnknown": True},
            "body",
            id="unknown_field",
        ),
        pytest.param(["not", "an", "object"], "body", id="array_body"),
    ],
)
def test_authenticate_refuses_what_the_validator_refuses(
    user_account_audit_client: UserAccountAuditClient, body: Any, field: str
) -> None:
    resp = user_account_audit_client.authenticate(
        body, session=user_account_audit_client.start_session()
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == VALIDATION_ERROR
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


def test_turnstile_token_is_accepted_when_captcha_is_off(
    user_account_audit_client: UserAccountAuditClient, module_account: Account
) -> None:
    body = password_body(module_account.password, module_account.email)
    body["cf-turnstile-response"] = "spec-audit-token"

    _assert_signed_in(
        user_account_audit_client.authenticate(
            body, session=user_account_audit_client.start_session()
        )
    )


def test_extra_credential_fields_and_the_query_string_are_ignored(
    user_account_audit_client: UserAccountAuditClient, module_account: Account
) -> None:
    body = password_body(module_account.password, module_account.email)
    body["credentials"]["specAuditUnknown"] = True

    with outside_request_contract(
        "the validator passes unknown credential fields through and drops unknown query keys"
    ):
        resp = user_account_audit_client.authenticate(
            body,
            session=user_account_audit_client.start_session(),
            params={"specAuditUnknown": "1"},
        )
        _assert_signed_in(resp)


def test_emailed_code_signs_in_once(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    account: Account,
    mailbox: None,
) -> None:
    code = emailed_sign_in_code(user_account_audit_client, account.email)

    session = sign_in_policy.session_under(PASSWORD_AND_OTP, user_account_audit_client)
    _assert_signed_in(
        user_account_audit_client.authenticate(otp_body(code, account.email), session=session)
    )

    # The code was cleared by the sign-in, so it now reads like a wrong one.
    session = sign_in_policy.session_under(PASSWORD_AND_OTP, user_account_audit_client)
    _assert_refused(
        user_account_audit_client.authenticate(otp_body(code, account.email), session=session),
        401,
        UNAUTHORIZED,
        WRONG_SIGN_IN_CODE,
    )


def test_wrong_code_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    account: Account,
    mailbox: None,
) -> None:
    code = emailed_sign_in_code(user_account_audit_client, account.email)
    wrong = "000000" if code != "000000" else "111111"

    session = sign_in_policy.session_under(PASSWORD_AND_OTP, user_account_audit_client)
    _assert_refused(
        user_account_audit_client.authenticate(otp_body(wrong, account.email), session=session),
        401,
        UNAUTHORIZED,
        WRONG_SIGN_IN_CODE,
    )


@pytest.mark.parametrize("known_account", [True, False], ids=["no_code_requested", "no_account"])
def test_code_nobody_was_sent_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    module_account: Account,
    stray_email: str,
    known_account: bool,
) -> None:
    email = module_account.email if known_account else stray_email
    session = sign_in_policy.session_under(PASSWORD_AND_OTP, user_account_audit_client)

    _assert_refused(
        user_account_audit_client.authenticate(otp_body("123456", email), session=session),
        401,
        UNAUTHORIZED,
        WRONG_SIGN_IN_CODE,
    )


def test_two_step_policy_answers_the_next_step_then_tokens(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    account: Account,
    mailbox: None,
) -> None:
    session = sign_in_policy.session_under(
        steps(["password"], ["otp"]), user_account_audit_client, account.email
    )

    first = user_account_audit_client.authenticate(
        password_body(account.password), session=session
    )
    assert first.status_code == 200, first.text[:500]
    assert_strict_openapi_exchange(first, ROUTE)
    assert first.json() == {
        "status": "success",
        "nextStep": 1,
        "allowedMethods": ["otp"],
        "authProviders": {},
    }

    # Step one's method is not accepted again at step two.
    _assert_refused(
        user_account_audit_client.authenticate(password_body(account.password), session=session),
        400,
        BAD_REQUEST,
        SIGN_IN_METHOD_NOT_ALLOWED,
    )

    code = emailed_sign_in_code(user_account_audit_client, account.email)
    _assert_signed_in(user_account_audit_client.authenticate(otp_body(code), session=session))


def test_next_step_lists_the_settings_of_its_providers(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    oauth_provider: OAuthProviderStub,
    module_account: Account,
) -> None:
    session = sign_in_policy.session_under(
        steps(["password"], ["oauth", "google", "microsoft", "azureAd"]),
        user_account_audit_client,
        module_account.email,
    )

    resp = user_account_audit_client.authenticate(
        password_body(module_account.password), session=session
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    answer = resp.json()
    assert answer["allowedMethods"] == ["oauth", "google", "microsoft", "azureAd"]
    assert set(answer["authProviders"]) == {"oauth", "google", "microsoft", "azureAd"}
    assert answer["authProviders"]["oauth"]["clientId"] == "spec-audit-client"
    assert "clientSecret" not in answer["authProviders"]["oauth"]


def test_oauth_access_token_signs_in(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    oauth_provider: OAuthProviderStub,
    module_account: Account,
) -> None:
    _, access_token = oauth_provider.issue(module_account.email)
    session = sign_in_policy.session_under(PASSWORD_AND_OAUTH, user_account_audit_client)

    _assert_signed_in(
        user_account_audit_client.authenticate(
            {"method": "oauth", "credentials": {"accessToken": access_token}}, session=session
        )
    )


@pytest.mark.parametrize(
    ("case", "status", "code", "message"),
    [
        pytest.param("token_the_provider_rejects", 401, UNAUTHORIZED, OAUTH_SIGN_IN_FAILED, id="rejected_token"),
        pytest.param("no_access_token", 400, BAD_REQUEST, OAUTH_SIGN_IN_FAILED, id="no_access_token"),
        pytest.param("no_email", 400, BAD_REQUEST, PROVIDER_SHARED_NO_EMAIL, id="provider_shares_no_email"),
        pytest.param("no_account", 400, BAD_REQUEST, ACCOUNT_NOT_FOUND, id="no_account_and_jit_off"),
    ],
)
def test_oauth_sign_in_that_cannot_complete(
    user_account_audit_client: UserAccountAuditClient,
    sign_in_policy: SignInPolicy,
    oauth_provider: OAuthProviderStub,
    stray_email: str,
    case: str,
    status: int,
    code: str,
    message: str,
) -> None:
    credentials: dict[str, Any] = {
        "token_the_provider_rejects": lambda: {"accessToken": "spec-audit-never-issued"},
        "no_access_token": lambda: {"idToken": "spec-audit-id-token"},
        "no_email": lambda: {"accessToken": oauth_provider.issue(None)[1]},
        "no_account": lambda: {"accessToken": oauth_provider.issue(stray_email)[1]},
    }[case]()
    session = sign_in_policy.session_under(PASSWORD_AND_OAUTH, user_account_audit_client)

    resp = user_account_audit_client.authenticate(
        {"method": "oauth", "credentials": credentials}, session=session
    )
    _assert_refused(resp, status, code, message)
