"""Strict OpenAPI audit of GET /api/v1/saml/signIn."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from helper.pipeshub_client import PipeshubClient
from saml_audit_support import (
    CODE_CHALLENGE,
    DESKTOP_STATE,
    SIGN_IN_ROUTE,
    SamlClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = SIGN_IN_ROUTE
assert ROUTE == "/api/v1/saml/signIn"

SESSION_TOKEN = "spec-audit-session-token"
DESKTOP_KEYS = {"client", "state", "codeChallenge"}


def _relay_state(location: str) -> dict[str, Any]:
    raw = parse_qs(urlsplit(location).query).get("RelayState", [""])[0]
    assert raw, f"IdP redirect carries no RelayState: {location[:300]}"
    return json.loads(base64.b64decode(raw))


@pytest.mark.parametrize(
    ("params", "desktop"),
    [
        pytest.param({"email": "spec-audit@example.com"}, False, id="email_only"),
        # The route has no validator, so email is optional.
        pytest.param({}, False, id="no_query"),
        pytest.param(
            {"email": "not-an-email", "sessionToken": SESSION_TOKEN},
            False,
            id="malformed_email_with_session_token",
        ),
        pytest.param(
            {
                "email": "spec-audit@example.com",
                "client": "desktop",
                "state": DESKTOP_STATE,
                "code_challenge": CODE_CHALLENGE,
            },
            True,
            id="desktop_flow",
        ),
    ],
)
def test_sign_in_redirects_to_idp(
    saml_client: SamlClient,
    pipeshub_client: PipeshubClient,
    saml_configured: bool,
    params: dict[str, str],
    desktop: bool,
) -> None:
    if not saml_configured:
        pytest.skip(
            "No SAML identity provider is configured on this deployment (the "
            'passport "saml" strategy is not registered), so there is no IdP to redirect to'
        )
    # Nothing in the query is validated; every variant goes to the IdP.
    resp = saml_client.sign_in(**params)
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    relay = _relay_state(resp.headers["Location"])
    assert relay["orgId"] == pipeshub_client.org_id
    assert relay.get("sessionToken") == params.get("sessionToken")
    if desktop:
        assert relay["client"] == "desktop"
        assert relay["state"] == DESKTOP_STATE
        assert relay["codeChallenge"] == CODE_CHALLENGE
    else:
        assert not DESKTOP_KEYS & relay.keys(), relay


@pytest.mark.xfail(
    strict=True,
    reason="API bug: GET /saml/signIn answers 500 INTERNAL_ERROR when SAML SSO is not configured",
)
def test_sign_in_without_saml_configured_is_not_found(
    saml_client: SamlClient, saml_configured: bool
) -> None:
    if saml_configured:
        pytest.skip("A SAML identity provider is configured on this deployment")
    # passport's "Unknown authentication strategy" error goes to next() and
    # reaches the global handler as an unknown error.
    resp = saml_client.sign_in(email="spec-audit@example.com")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
