"""Strict OpenAPI audit of POST /api/v1/service-accounts.

authenticate -> rate limiter -> requireScopes(user:invite) -> userAdminCheck
-> zod createServiceAccountSchema (trims, lowercases the slug, strips unknown fields) -> create.
"""

from __future__ import annotations

import uuid
from typing import Any

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
    request_with_token,
    unique_slug,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


def _unique_slug_of_length(length: int) -> str:
    return ("sa-" + uuid.uuid4().hex + uuid.uuid4().hex)[:length]


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
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)


def test_lengths_are_counted_after_trimming(
    service_accounts_client: ServiceAccountsClient,
    track_service_account: TrackServiceAccount,
) -> None:
    slug = _unique_slug_of_length(48)
    body = {
        "slug": f"   {slug}  ",
        "fullName": "     " + "z" * 100 + "  ",
        "description": " " + "d" * 500 + "   ",
    }

    resp = service_accounts_client.create(json=body)

    assert resp.status_code == 201, resp.text[:500]
    account = resp.json()
    track_service_account(account["id"])
    assert account["slug"] == slug
    assert account["fullName"] == "z" * 100
    assert account["description"] == "d" * 500
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"slug": "-not--a-slug-"}, "body.slug", id="slug-bad-shape"),
        pytest.param({"slug": "ab"}, "body.slug", id="slug-under-3"),
        pytest.param({"slug": "  ab  "}, "body.slug", id="slug-under-3-after-trim"),
        pytest.param({"slug": "a" * 49}, "body.slug", id="slug-over-48"),
        pytest.param({"slug": "snake_case"}, "body.slug", id="slug-underscore"),
        pytest.param({"slug": 123}, "body.slug", id="slug-not-a-string"),
        pytest.param({"slug": None}, "body.slug", id="slug-missing"),
        pytest.param({"fullName": "   "}, "body.fullName", id="full-name-blank"),
        pytest.param({"fullName": "x" * 101}, "body.fullName", id="full-name-over-100"),
        pytest.param({"fullName": None}, "body.fullName", id="full-name-missing"),
        pytest.param({"description": "d" * 501}, "body.description", id="description-over-500"),
        pytest.param({"description": 5}, "body.description", id="description-not-a-string"),
    ],
)
def test_invalid_body_is_a_validation_error(
    service_accounts_client: ServiceAccountsClient,
    track_service_account: TrackServiceAccount,
    overrides: dict[str, Any],
    field: str,
) -> None:
    # An override of None leaves that field out of the body.
    body = create_body(**{k: v for k, v in overrides.items() if v is not None})
    body = {k: v for k, v in body.items() if overrides.get(k, "") is not None}

    resp = service_accounts_client.create(json=body)
    if resp.status_code == 201:
        track_service_account(resp.json()["id"])

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert field in [e["field"] for e in error["metadata"]["errors"]], error
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)


def test_create_without_a_body_is_a_validation_error(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.create()

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)


def test_unknown_body_fields_are_ignored(
    service_accounts_client: ServiceAccountsClient,
    track_service_account: TrackServiceAccount,
) -> None:
    body = create_body(role="admin", isDisabled=True, email="someone@example.com")

    with outside_request_contract("undocumented body fields; zod strips them"):
        resp = service_accounts_client.create(json=body)
        assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)

    assert resp.status_code == 201, resp.text[:500]
    account = resp.json()
    track_service_account(account["id"])
    assert account["isDisabled"] is False
    assert account["email"].endswith(f"@{SERVICE_ACCOUNT_EMAIL_DOMAIN}")


def test_reusing_a_deleted_name_restores_the_same_account(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
    track_service_account: TrackServiceAccount,
) -> None:
    old = seed_service_account(disabled=True, description="before deletion")
    removed = service_accounts_client.remove(old["id"])
    assert removed.status_code == 204, removed.text[:500]

    resp = service_accounts_client.create(json=create_body(slug=old["slug"], fullName="Restored"))

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)
    restored = resp.json()
    track_service_account(restored["id"])
    assert restored["id"] == old["id"]
    assert restored["email"] == old["email"]
    assert restored["fullName"] == "Restored"
    assert restored["isDisabled"] is False
    assert "description" not in restored


def test_create_without_token_is_unauthorized(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.create(auth=False, json=create_body())

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)


def test_create_as_member_is_forbidden(second_user: SecondUser) -> None:
    # The admin gate runs before validation, so a valid body never creates anything.
    resp = request_as(second_user, "POST", json=create_body())

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)


def test_create_with_a_token_lacking_user_invite_is_forbidden(
    service_accounts_client: ServiceAccountsClient, kb_read_pat: str
) -> None:
    resp = request_with_token(
        service_accounts_client._client.base_url, kb_read_pat, "POST", json=create_body()
    )

    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)


def test_create_with_live_slug_is_conflict(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account()

    resp = service_accounts_client.create(json=create_body(slug=account["slug"].upper()))

    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROOT_TEMPLATE)
