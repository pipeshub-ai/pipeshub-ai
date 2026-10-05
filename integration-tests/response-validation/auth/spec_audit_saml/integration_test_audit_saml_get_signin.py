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
        # The route has no validator: the spec's "required" email is never checked.
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
            {
                "email": "spec-audit@example.com",
                "client": "desktop",
                "state": "no-phd-prefix",
                "code_challenge": "too-short",
            },
            False,
            id="desktop_flow_bad_state_ignored",
        ),
    ],
)
def test_sign_in_redirects_to_idp_or_fails_without_strategy(
    saml_client: SamlClient,
    pipeshub_client: PipeshubClient,
    params: dict[str, str],
    desktop: bool,
) -> None:
    resp = saml_client.sign_in(**params)

    # Nothing in the query is validated, so the outcome depends only on the
    # deployment: 302 to the IdP when the passport "saml" strategy is
    # registered, otherwise passport's "Unknown authentication strategy" error
    # reaches the global handler as a 500.
    assert resp.status_code in (302, 500), resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    if resp.status_code == 500:
        assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
        return

    relay = _relay_state(resp.headers["Location"])
    assert relay["orgId"] == pipeshub_client.org_id
    assert relay.get("sessionToken") == params.get("sessionToken")
    if desktop:
        assert relay["client"] == "desktop"
        assert relay["state"] == DESKTOP_STATE
        assert relay["codeChallenge"] == CODE_CHALLENGE
    else:
        assert not DESKTOP_KEYS & relay.keys(), relay
