"""Strict OpenAPI audit of POST /api/v1/saml/signIn/callback."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from saml_audit_support import (
    CODE_CHALLENGE,
    DESKTOP_STATE,
    SIGN_IN_CALLBACK_ROUTE,
    SamlClient,
    relay_state,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = SIGN_IN_CALLBACK_ROUTE

# SAML_LOGOUT_UNSUPPORTED_MESSAGE in saml.controller.ts.
LOGOUT_UNSUPPORTED = (
    "Signing out through your identity provider isn't supported. "
    "To sign out, use Sign out in PipesHub."
)
LOGIN_PATH = "/login"
DESKTOP_SUCCESS_PATH = "/auth/sign-in/samlSso/success"
NOT_A_SAML_RESPONSE = "c3BlYy1hdWRpdC1ub3QtYS1zYW1sLXJlc3BvbnNl"


def _redirect(resp: requests.Response) -> tuple[str, dict[str, list[str]]]:
    target = urlparse(resp.headers.get("Location", ""))
    return target.path, parse_qs(target.query)


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
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    path, query = _redirect(resp)
    assert path.endswith(LOGIN_PATH), resp.headers.get("Location")
    assert query == {"saml_error": [LOGOUT_UNSUPPORTED]}


def test_desktop_relay_state_sends_error_to_success_page(
    saml_client: SamlClient,
) -> None:
    resp = saml_client.sign_in_callback(
        {
            "SAMLRequest": "spec-audit",
            "RelayState": relay_state(
                client="desktop", state=DESKTOP_STATE, codeChallenge=CODE_CHALLENGE
            ),
        }
    )
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    path, query = _redirect(resp)
    assert path.endswith(DESKTOP_SUCCESS_PATH), resp.headers.get("Location")
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
    assert_strict_openapi_response(resp, ROUTE)

    path, query = _redirect(resp)
    assert path.endswith(LOGIN_PATH), resp.headers.get("Location")
    # The code is passport's raw message: unknown strategy without SAML, parse error with it.
    assert query.get("saml_error"), resp.headers.get("Location")
    assert "accessToken" not in resp.cookies


def test_empty_body_never_reaches_json_error_handler(
    saml_client: SamlClient,
) -> None:
    # Without SAML configured this is the unknown-strategy redirect to /login;
    # with it, passport-saml falls back to a login request and redirects to the IdP.
    resp = saml_client.sign_in_callback()
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.headers.get("Location")
    assert "accessToken" not in resp.cookies
