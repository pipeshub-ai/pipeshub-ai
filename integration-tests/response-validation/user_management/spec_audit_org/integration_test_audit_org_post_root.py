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
    VALID_PASSWORD,
    OrgBody,
    error_of,
    org_creation_body,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

FULL_ADDRESS = {
    "addressLine1": "1 Audit Way",
    "city": "Pune",
    "state": "MH",
    "country": "IN",
    "postCode": "411001",
}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(org_creation_body(), id="individual-minimal"),
        pytest.param(
            org_creation_body(
                "business", shortName="SA", sendEmail=False, permanentAddress=FULL_ADDRESS
            ),
            id="business-every-field",
        ),
    ],
)
def test_valid_body_is_refused_because_org_exists_even_with_a_bad_token(
    org_client: OrgClient, org_intact: str, body: OrgBody
) -> None:
    # A garbage bearer would be a 401 on an authenticated route; here it is ignored.
    resp = org_client.post("/", json=body, auth=False, headers=INVALID_BEARER)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert error_of(resp)["message"] == ORG_EXISTS_MESSAGE


def test_unknown_fields_are_dropped_by_the_validator(org_client: OrgClient, org_intact: str) -> None:
    body = org_creation_body(plan="enterprise", permanentAddress={"city": "Pune", "_id": "x"})
    with outside_request_contract("proves unknown fields pass the validator and are stripped"):
        resp = org_client.post("/", json=body, auth=False)
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert error_of(resp)["message"] == ORG_EXISTS_MESSAGE


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.accountType", id="empty-body"),
        pytest.param(org_creation_body(accountType="enterprise"), "body.accountType", id="unknown-account-type"),
        pytest.param(org_creation_body(adminFullName=""), "body.adminFullName", id="empty-admin-name"),
        pytest.param(org_creation_body(password="Sh0rt!"), "body.password", id="password-too-short"),
        pytest.param(org_creation_body(password="alllowercase1!"), "body.password", id="password-without-uppercase"),
        pytest.param(org_creation_body(password="NoSpecial123"), "body.password", id="password-without-special"),
        pytest.param(
            org_creation_body(password=VALID_PASSWORD + "a" * 60), "body.password", id="password-over-72-bytes"
        ),
        pytest.param(
            org_creation_body("business", registeredName=None),
            "body.registeredName",
            id="business-without-registered-name",
        ),
        pytest.param(
            org_creation_body("business", registeredName=""),
            "body.registeredName",
            id="business-with-empty-registered-name",
        ),
        pytest.param(org_creation_body(contactEmail="not-an-email"), "body.contactEmail", id="malformed-contact-email"),
        pytest.param(org_creation_body(sendEmail="yes"), "body.sendEmail", id="send-email-not-boolean"),
        pytest.param(
            org_creation_body(permanentAddress={"city": 5}), "body.permanentAddress.city", id="address-city-not-string"
        ),
        pytest.param(org_creation_body(permanentAddress="Pune"), "body.permanentAddress", id="address-not-object"),
    ],
)
def test_invalid_body_fails_validation(
    org_client: OrgClient, org_intact: str, body: OrgBody, field: str
) -> None:
    resp = org_client.post("/", json=body, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR"
    assert field in {e["field"] for e in error["metadata"]["errors"]}, error
