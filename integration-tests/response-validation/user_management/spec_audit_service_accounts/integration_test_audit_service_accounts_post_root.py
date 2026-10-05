"""Strict OpenAPI audit of POST /api/v1/service-accounts."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from service_accounts_audit_support import (
    ROOT_TEMPLATE,
    SERVICE_ACCOUNT_EMAIL_DOMAIN,
    SeedServiceAccount,
    ServiceAccountsClient,
    TrackServiceAccount,
    create_body,
    request_as,
    unique_slug,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit


def test_create_returns_normalised_account(
    service_accounts_client: ServiceAccountsClient,
    track_service_account: TrackServiceAccount,
) -> None:
    slug = unique_slug()
    body = create_body(
        slug=f"  {slug.upper()}  ",
        fullName="  Spec audit created  ",
        description="  created by the spec audit  ",
    )

    resp = service_accounts_client.create(json=body)

    assert resp.status_code == 201, resp.text[:500]
    account = resp.json()
    track_service_account(account["id"])
    assert account["slug"] == slug
    assert account["fullName"] == "Spec audit created"
    assert account["description"] == "created by the spec audit"
    assert account["isDisabled"] is False
    assert account["email"].startswith(f"svc-{slug}-")
    assert account["email"].endswith(f"@{SERVICE_ACCOUNT_EMAIL_DOMAIN}")
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)


def test_create_without_token_is_unauthorized(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.create(auth=False, json=create_body())

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)


def test_create_as_member_is_forbidden(second_user: SecondUser) -> None:
    # The admin gate runs before validation, so a valid body never creates anything.
    resp = request_as(second_user, "POST", json=create_body())

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)


def test_create_with_invalid_slug_is_bad_request(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.create(json=create_body(slug="-not--a-slug-"))

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)


def test_create_with_live_slug_is_conflict(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account()

    resp = service_accounts_client.create(json=create_body(slug=account["slug"]))

    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)
