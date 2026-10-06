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
    IDP_ENTRY_POINT,
    SIGN_IN_ROUTE,
    DummyIdp,
    SamlClient,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = SIGN_IN_ROUTE
assert ROUTE == "/api/v1/saml/signIn"

SESSION_TOKEN = "spec-audit-session-token"
DESKTOP_KEYS = {"client", "state", "codeChallenge"}


def _relay_state(location: str) -> dict[str, Any]:
    raw = parse_qs(urlsplit(location).query).get("RelayState", [""])[0]
    assert raw, f"IdP redirect carries no RelayState: {location[:300]}"
    return json.loads(base64.b64decode(raw))


# Defined first: it has to run before any test here saves an IdP.
def test_sign_in_without_a_registered_idp_is_an_internal_error(
    saml_client: SamlClient, saml_configured: bool
) -> None:
    if saml_configured:
        pytest.skip(
            "An IdP is registered with this Node process. passport keeps a registered "
            "strategy until the process restarts, so once any SSO setting has been saved "
            "(by this suite's own dummy_idp fixture too) the never-configured state cannot "
            "come back in the same run"
        )
    # passport's "Unknown authentication strategy" error reaches the global
    # handler as an unknown error, not as a 404.
    resp = saml_client.sign_in(email="spec-audit@example.com")
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


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
        pytest.param(
            {"client": "desktop", "state": DESKTOP_STATE},
            False,
            id="desktop_without_code_challenge",
        ),
        pytest.param(
            {"client": "desktop", "state": "not-a-desktop-state", "code_challenge": CODE_CHALLENGE},
            False,
            id="desktop_with_malformed_state",
        ),
    ],
)
def test_sign_in_redirects_to_idp(
    saml_client: SamlClient,
    pipeshub_client: PipeshubClient,
    dummy_idp: DummyIdp,
    params: dict[str, str],
    desktop: bool,
) -> None:
    # Nothing in the query is validated; every variant goes to the IdP.
    resp = saml_client.sign_in(**params)
    assert resp.status_code == 302, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    location = resp.headers["Location"]
    assert location.startswith(f"{IDP_ENTRY_POINT}?"), location[:200]
    assert parse_qs(urlsplit(location).query).get("SAMLRequest")
    relay = _relay_state(location)
    assert relay["orgId"] == pipeshub_client.org_id
    assert relay.get("sessionToken") == params.get("sessionToken")
    if desktop:
        assert relay["client"] == "desktop"
        assert relay["state"] == DESKTOP_STATE
        assert relay["codeChallenge"] == CODE_CHALLENGE
    else:
        assert not DESKTOP_KEYS & relay.keys(), relay


def test_sign_in_ignores_what_it_does_not_read(
    saml_client: SamlClient, dummy_idp: DummyIdp
) -> None:
    with outside_request_contract(
        "the route has no validator: any other client value and unknown query keys are ignored"
    ):
        resp = saml_client.sign_in(client="web", specAuditUnknown="1")
        assert resp.status_code == 302, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert not DESKTOP_KEYS & _relay_state(resp.headers["Location"]).keys()
