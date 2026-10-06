"""Strict OpenAPI audit of POST /api/v1/saml/signIn/callback.

Every outcome is a 302. Two of them have an empty body, which the strict checker cannot
accept next to the documented text bodies, so they are described in the spec and not
called here: the redirect to the IdP when the form carries neither ``SAMLResponse`` nor
``SAMLRequest``, and any redirect asked for with an ``Accept`` header that excludes text.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from saml_audit_support import (
    CODE_CHALLENGE,
    DESKTOP_STATE,
    LOGIN_PATH,
    LOGOUT_UNSUPPORTED,
    NOT_A_SAML_RESPONSE,
    SIGN_IN_CALLBACK_ROUTE,
    SUCCESS_PATH,
    DummyIdp,
    SamlClient,
    desktop_relay_state,
    redirect_target,
    relay_state,
)
from strict_openapi import assert_strict_openapi_exchange
from user_account_audit_support import (
    DisposableMember,
    SignInPolicy,
    UserAccountAuditClient,
    unused_email,
)

pytestmark = pytest.mark.spec_audit

ROUTE = SIGN_IN_CALLBACK_ROUTE
TOKEN_COOKIES = ["accessToken", "refreshToken"]


def _cookies_set(resp: requests.Response) -> list[str]:
    return sorted(c.split("=", 1)[0] for c in resp.raw.headers.getlist("Set-Cookie"))


def _assert_error_redirect(resp: requests.Response, code: str) -> None:
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    path, query = redirect_target(resp)
    assert path.endswith(LOGIN_PATH), resp.headers.get("Location")
    assert query == {"saml_error": [code]}
    assert _cookies_set(resp) == []


@pytest.mark.parametrize(
    "call",
    [
        pytest.param({"form": {"SAMLRequest": "spec-audit"}}, id="body"),
        pytest.param({"params": {"SAMLRequest": "spec-audit"}}, id="query"),
    ],
)
def test_logout_request_is_refused_with_login_redirect(
    saml_client: SamlClient, call: dict[str, Any]
) -> None:
    resp = saml_client.sign_in_callback(**call)
    _assert_error_redirect(resp, LOGOUT_UNSUPPORTED)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            {"form": {"SAMLRequest": "spec-audit", "RelayState": desktop_relay_state()}},
            id="relay_state_in_body",
        ),
        pytest.param(
            {"form": {"SAMLRequest": "spec-audit"}, "params": {"RelayState": desktop_relay_state()}},
            id="relay_state_in_query",
        ),
    ],
)
def test_desktop_relay_state_sends_error_to_success_page(
    saml_client: SamlClient, call: dict[str, Any]
) -> None:
    resp = saml_client.sign_in_callback(**call)
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    path, query = redirect_target(resp)
    assert path.endswith(SUCCESS_PATH), resp.headers.get("Location")
    assert query == {"state": [DESKTOP_STATE], "saml_error": [LOGOUT_UNSUPPORTED]}


def test_unverifiable_saml_response_redirects_to_login(
    saml_client: SamlClient,
) -> None:
    # A desktop RelayState whose state fails the phd. pattern is treated as a web sign-in.
    resp = saml_client.sign_in_callback(
        {
            "SAMLResponse": NOT_A_SAML_RESPONSE,
            "RelayState": relay_state(
                client="desktop", state="not-a-desktop-state", codeChallenge=CODE_CHALLENGE
            ),
        }
    )
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    path, query = redirect_target(resp)
    assert path.endswith(LOGIN_PATH), resp.headers.get("Location")
    # The code is passport's raw message: unknown strategy without SAML, parse error with it.
    assert query.get("saml_error"), resp.headers.get("Location")
    assert _cookies_set(resp) == []


def test_assertion_is_refused_while_saml_is_not_a_sign_in_method(
    saml_client: SamlClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_member: DisposableMember,
    sign_in_policy: SignInPolicy,
) -> None:
    assert not any(
        method["type"] == "samlSso"
        for step in sign_in_policy.current()
        for method in step["allowedMethods"]
    ), "another test left samlSso in the org's sign-in policy"

    resp = saml_client.sign_in_callback(
        {"SAMLResponse": dummy_idp.response(saml_member.email, service_provider)}
    )
    _assert_error_redirect(resp, "saml_sso_disabled")


@pytest.mark.parametrize(
    ("accept", "content_type", "body_start"),
    [
        pytest.param("*/*", "text/plain", "Found. Redirecting to ", id="any"),
        pytest.param("text/html", "text/html", "<p>Found. Redirecting to ", id="browser"),
    ],
)
def test_valid_assertion_signs_the_account_in_with_cookies(
    saml_client: SamlClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_member: DisposableMember,
    saml_sign_in_allowed: None,
    accept: str,
    content_type: str,
    body_start: str,
) -> None:
    resp = saml_client.sign_in_callback(
        {"SAMLResponse": dummy_idp.response(saml_member.email, service_provider)},
        headers={"Accept": accept},
    )
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    path, query = redirect_target(resp)
    assert path.endswith(SUCCESS_PATH), resp.headers.get("Location")
    assert query == {}
    assert _cookies_set(resp) == TOKEN_COOKIES
    assert resp.headers["Content-Type"].startswith(content_type)
    assert resp.text.startswith(body_start)


def test_valid_assertion_resumes_the_session_named_in_relay_state(
    saml_client: SamlClient,
    user_account_audit_client: UserAccountAuditClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_member: DisposableMember,
    saml_sign_in_allowed: None,
) -> None:
    session = user_account_audit_client.start_session()

    resp = saml_client.sign_in_callback(
        {
            "SAMLResponse": dummy_idp.response(saml_member.email, service_provider),
            "RelayState": relay_state(sessionToken=session),
        }
    )
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert redirect_target(resp)[0].endswith(SUCCESS_PATH), resp.headers.get("Location")
    assert _cookies_set(resp) == TOKEN_COOKIES

    # Completing the sign-in ends the session it resumed.
    reused = user_account_audit_client.authenticate(
        {"method": "password", "credentials": {"password": "spec-audit"}}, session=session
    )
    assert reused.status_code == 401, reused.text[:500]


def test_desktop_sign_in_gets_a_handoff_code_instead_of_cookies(
    saml_client: SamlClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_member: DisposableMember,
    saml_sign_in_allowed: None,
) -> None:
    resp = saml_client.sign_in_callback(
        {
            "SAMLResponse": dummy_idp.response(saml_member.email, service_provider),
            "RelayState": desktop_relay_state(),
        }
    )
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    path, query = redirect_target(resp)
    assert path.endswith(SUCCESS_PATH), resp.headers.get("Location")
    assert set(query) == {"state", "code"}
    assert query["state"] == [DESKTOP_STATE]
    code = query["code"][0]
    assert len(code) == 64 and set(code) <= set("0123456789abcdef"), code
    assert _cookies_set(resp) == []


def test_unknown_email_is_refused_while_jit_is_off(
    saml_client: SamlClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_sign_in_allowed: None,
) -> None:
    resp = saml_client.sign_in_callback(
        {"SAMLResponse": dummy_idp.response(unused_email("spec-audit-saml-nobody"), service_provider)}
    )
    _assert_error_redirect(resp, "jit_disabled")


def test_assertion_without_a_usable_email_is_an_unknown_error(
    saml_client: SamlClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_member: DisposableMember,
    saml_sign_in_allowed: None,
) -> None:
    resp = saml_client.sign_in_callback(
        {
            "SAMLResponse": dummy_idp.response(
                saml_member.email, service_provider, email_attribute=None
            )
        }
    )
    _assert_error_redirect(resp, "unknown")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param("unsigned", "Invalid signature", id="unsigned"),
        pytest.param("other_idp", "Invalid signature", id="signed_by_another_idp"),
        pytest.param("expired", "SAML assertion expired: clocks skewed too much", id="expired"),
        pytest.param("audience", "SAML assertion audience mismatch", id="another_audience"),
    ],
)
def test_assertion_passport_rejects_carries_its_raw_message(
    saml_client: SamlClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_member: DisposableMember,
    saml_sign_in_allowed: None,
    case: str,
    message: str,
) -> None:
    email = saml_member.email
    saml_response = {
        "unsigned": lambda: dummy_idp.response(email, service_provider, signed=False),
        "other_idp": lambda: DummyIdp().response(email, service_provider),
        "expired": lambda: dummy_idp.response(email, service_provider, valid_for_seconds=-30),
        "audience": lambda: dummy_idp.response(email, "https://elsewhere.example/saml"),
    }[case]()

    resp = saml_client.sign_in_callback({"SAMLResponse": saml_response})
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    path, query = redirect_target(resp)
    assert path.endswith(LOGIN_PATH), resp.headers.get("Location")
    assert query["saml_error"][0].startswith(message), query
    assert _cookies_set(resp) == []
