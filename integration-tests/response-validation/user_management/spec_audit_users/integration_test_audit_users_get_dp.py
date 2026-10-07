"""Strict OpenAPI audit of GET /api/v1/users/dp."""

from __future__ import annotations

import pytest
import requests
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    INVALID_BEARER,
    TINY_PNG,
    display_picture_request,
    image_bytes,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/dp"

NOT_FOUND = {"errorMessage": "User pic not found"}


@pytest.mark.parametrize(
    ("data", "mime"),
    [(TINY_PNG, "image/png"), (image_bytes("WEBP"), "image/webp"), (image_bytes("GIF"), "image/gif")],
    ids=["png", "webp", "gif"],
)
def test_the_picture_is_always_served_as_jpeg(no_picture: SecondUser, data: bytes, mime: str) -> None:
    uploaded = display_picture_request(no_picture, "PUT", files={"file": ("dp", data, mime)})
    assert uploaded.status_code == 201, uploaded.text[:500]

    resp = display_picture_request(no_picture, "GET")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers["Content-Type"] == "image/jpeg", resp.headers
    assert resp.content == uploaded.content
    assert resp.content.startswith(b"\xff\xd8\xff")


def test_no_picture_is_a_200_message(no_picture: SecondUser) -> None:
    resp = display_picture_request(no_picture, "GET")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == NOT_FOUND


def test_a_query_is_ignored(no_picture: SecondUser) -> None:
    uploaded = display_picture_request(no_picture, "PUT", files={"file": ("dp.png", TINY_PNG, "image/png")})
    assert uploaded.status_code == 201, uploaded.text[:500]
    with outside_request_contract("the route takes no query; this shows another user's id is not read"):
        resp = display_picture_request(no_picture, "GET", params={"userId": "0123456789abcdef01234567"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.content == uploaded.content


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    no_picture: SecondUser, headers: dict[str, str], message: str
) -> None:
    resp = requests.get(f"{no_picture.base_url}{ROUTE}", headers=headers, timeout=no_picture.timeout)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
