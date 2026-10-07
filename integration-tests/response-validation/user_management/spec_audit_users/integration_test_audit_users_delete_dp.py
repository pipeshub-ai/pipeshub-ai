"""Strict OpenAPI audit of DELETE /api/v1/users/dp."""

from __future__ import annotations

import pytest
import requests
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    INVALID_BEARER,
    TINY_PNG,
    display_picture_request,
    read_display_pictures,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/dp"

NOT_FOUND = {"errorMessage": "User display picture not found"}


def _upload(user: SecondUser) -> None:
    resp = display_picture_request(user, "PUT", files={"file": ("dp.png", TINY_PNG, "image/png")})
    assert resp.status_code == 201, resp.text[:500]


def test_removing_a_picture_returns_the_cleared_record(no_picture: SecondUser) -> None:
    _upload(no_picture)
    resp = display_picture_request(no_picture, "DELETE")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"_id", "userId", "orgId", "pic", "mimeType", "__v"}, body
    assert (body["userId"], body["pic"], body["mimeType"]) == (no_picture.user_id, None, None), body

    fetched = display_picture_request(no_picture, "GET")
    assert fetched.status_code == 200, fetched.text[:500]
    assert_strict_openapi_exchange(fetched, ROUTE)
    assert fetched.json() == {"errorMessage": "User pic not found"}


def test_removing_again_returns_the_cleared_record_again(no_picture: SecondUser) -> None:
    # The record is kept with pic null, so a second removal is not reported as "not found".
    _upload(no_picture)
    first = display_picture_request(no_picture, "DELETE")
    assert first.status_code == 200, first.text[:500]
    again = display_picture_request(no_picture, "DELETE")
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert again.json() == first.json()
    (stored,) = read_display_pictures(no_picture.user_id)
    assert stored["pic"] is None, stored


def test_a_user_who_never_had_a_picture_gets_a_200_message(no_picture: SecondUser) -> None:
    resp = display_picture_request(no_picture, "DELETE")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NOT_FOUND
    assert read_display_pictures(no_picture.user_id) == []


def test_a_body_and_query_are_ignored(no_picture: SecondUser) -> None:
    _upload(no_picture)
    with outside_request_contract("the route reads no body or query; this shows both are ignored"):
        resp = display_picture_request(
            no_picture,
            "DELETE",
            params={"userId": "0123456789abcdef01234567"},
            json={"userId": "0123456789abcdef01234567"},
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["userId"] == no_picture.user_id, resp.json()


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    no_picture: SecondUser, headers: dict[str, str], message: str
) -> None:
    _upload(no_picture)
    resp = requests.delete(
        f"{no_picture.base_url}{ROUTE}", headers=headers, timeout=no_picture.timeout
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
    (stored,) = read_display_pictures(no_picture.user_id)
    assert stored["pic"], "the picture should survive a refused removal"
