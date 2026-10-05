"""Strict OpenAPI audit of POST /api/v1/org.

The route has no auth middleware, and the org count is checked before any
write, so on a stack that already has an org every well-formed call is a 400.
"""

from __future__ import annotations

import pytest
from helper.clients.org_client import OrgClient
from org_audit_support import (
    INVALID_BEARER,
    ORG_EXISTS_MESSAGE,
    ORG_ROUTE,
    OrgBody,
    org_creation_body,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit


def test_valid_body_is_refused_because_org_exists_even_with_a_bad_token(
    org_client: OrgClient, org_intact: str
) -> None:
    # A garbage bearer would be a 401 on an authenticated route; here it is ignored.
    resp = org_client.post(
        "/",
        json=org_creation_body("business", shortName="SA", sendEmail=False),
        auth=False,
        headers=INVALID_BEARER,
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ORG_ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert error["message"] == ORG_EXISTS_MESSAGE


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty-body"),
        pytest.param(
            org_creation_body(password="alllowercase1!"), id="password-without-uppercase"
        ),
        pytest.param(
            org_creation_body("business", registeredName=None),
            id="business-without-registered-name",
        ),
        pytest.param(
            org_creation_body(contactEmail="not-an-email"), id="malformed-contact-email"
        ),
    ],
)
def test_invalid_body_fails_validation(
    org_client: OrgClient, org_intact: str, body: OrgBody
) -> None:
    resp = org_client.post("/", json=body, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ORG_ROUTE)
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
