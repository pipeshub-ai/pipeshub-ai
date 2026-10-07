"""Strict OpenAPI audit of PUT /api/v1/org.

Every success test restores the org's profile fields straight in Mongo, because
the API cannot unset a field it has set.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser
from org_audit_support import INVALID_BEARER, ORG_ROUTE, ScopedCaller, error_of, request_as
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

UPDATED_MESSAGE = "Organization updated successfully"


def _put(org_client: OrgClient, body: Any) -> Any:
    resp = org_client.put("/", json=body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert resp.json()["message"] == UPDATED_MESSAGE
    return resp.json()["data"]


def test_every_field_is_updated(org_client: OrgClient, org_profile_restored: str) -> None:
    tag = uuid.uuid4().hex[:8]
    address = {"addressLine1": f"{tag} Audit Way", "city": "Pune", "state": "MH", "country": "IN", "postCode": "411001"}
    data = _put(
        org_client,
        {
            "contactEmail": f"spec-audit-{tag}@example.com",
            "registeredName": f"Spec Audit {tag}",
            "shortName": f"SA{tag}",
            "permanentAddress": address,
        },
    )
    assert data["_id"] == org_profile_restored
    assert data["contactEmail"] == f"spec-audit-{tag}@example.com"
    assert data["registeredName"] == f"Spec Audit {tag}"
    assert data["shortName"] == f"SA{tag}"
    assert {k: v for k, v in data["permanentAddress"].items() if k != "_id"} == address


def test_contact_email_is_stored_lower_case(org_client: OrgClient, org_profile_restored: str) -> None:
    tag = uuid.uuid4().hex[:8]
    data = _put(org_client, {"contactEmail": f"Spec-Audit-{tag}@Example.COM"})
    assert data["contactEmail"] == f"spec-audit-{tag}@example.com"


def test_address_is_replaced_not_merged(org_client: OrgClient, org_profile_restored: str) -> None:
    _put(org_client, {"permanentAddress": {"addressLine1": "1 Old Road", "city": "Pune"}})
    data = _put(org_client, {"permanentAddress": {"city": "Mumbai"}})
    assert data["permanentAddress"].get("city") == "Mumbai"
    assert "addressLine1" not in data["permanentAddress"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty-body"),
        pytest.param({"registeredName": "", "shortName": "", "contactEmail": None}, id="empty-strings"),
    ],
)
def test_empty_values_change_nothing(org_client: OrgClient, org_profile_restored: str, body: Any) -> None:
    before = org_client.get_organization().json()
    if body.get("contactEmail", 0) is None:
        with outside_request_contract("proves a null field is refused by the validator"):
            resp = org_client.put("/", json=body)
            assert resp.status_code == 400, resp.text[:500]
            assert_strict_openapi_exchange(resp, ORG_ROUTE)
        body = {k: v for k, v in body.items() if v is not None}
    data = _put(org_client, body)
    for field in ("registeredName", "shortName", "contactEmail", "permanentAddress"):
        assert data.get(field) == before.get(field), field


def test_no_body_is_treated_like_an_empty_object(org_client: OrgClient, org_profile_restored: str) -> None:
    before = org_client.get_organization().json()
    resp = org_client.put("/")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert resp.json()["message"] == UPDATED_MESSAGE
    data = resp.json()["data"]
    for field in ("registeredName", "shortName", "contactEmail", "permanentAddress"):
        assert data.get(field) == before.get(field), field


def test_unknown_fields_are_stripped(org_client: OrgClient, org_profile_restored: str) -> None:
    sent_id = "0123456789abcdef01234567"
    body = {
        "accountType": "business",
        "domain": "evil.example",
        "permanentAddress": {"_id": sent_id, "city": "Pune"},
    }
    before = org_client.get_organization().json()
    with outside_request_contract("proves fields outside the update schema are dropped, _id in the address too"):
        resp = org_client.put("/", json=body)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ORG_ROUTE)
    data = resp.json()["data"]
    assert data["accountType"] == before["accountType"]
    assert data["domain"] == before["domain"]
    assert data["permanentAddress"]["city"] == "Pune"
    assert data["permanentAddress"].get("_id") != sent_id


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"contactEmail": "not-an-email"}, "body.contactEmail", id="malformed-contact-email"),
        pytest.param({"shortName": 5}, "body.shortName", id="short-name-not-string"),
        pytest.param({"registeredName": ["x"]}, "body.registeredName", id="registered-name-not-string"),
        pytest.param({"permanentAddress": "Pune"}, "body.permanentAddress", id="address-not-object"),
        pytest.param({"permanentAddress": {"city": 5}}, "body.permanentAddress.city", id="address-city-not-string"),
    ],
)
def test_invalid_body_fails_validation(
    org_client: OrgClient, org_profile_restored: str, body: Any, field: str
) -> None:
    resp = org_client.put("/", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR"
    assert field in {e["field"] for e in error["metadata"]["errors"]}, error


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(
    org_client: OrgClient, org_intact: str, headers: dict[str, str]
) -> None:
    resp = org_client.put("/", auth=False, headers=headers, json={"shortName": "x"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)


@pytest.mark.parametrize(
    "body",
    [pytest.param({"shortName": "x"}, id="valid-body"), pytest.param({"contactEmail": "bad"}, id="invalid-body")],
)
def test_member_is_forbidden_before_the_body_is_validated(
    second_user: SecondUser, org_profile_restored: str, body: Any
) -> None:
    resp = request_as(second_user, "PUT", json=body)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert error_of(resp)["code"] == "HTTP_FORBIDDEN"


def test_oauth_token_without_org_write_is_forbidden(narrow_scope: ScopedCaller, org_profile_restored: str) -> None:
    resp = narrow_scope("PUT", json={"shortName": "x"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ORG_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:write"
