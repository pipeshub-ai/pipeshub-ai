"""Strict OpenAPI audit of PUT /api/v1/users/dp."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from users_audit_support import (
    INVALID_BEARER,
    TINY_PNG,
    display_picture_request,
    image_bytes,
    noisy_png,
    read_display_pictures,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/dp"

JPEG_MAGIC = b"\xff\xd8\xff"


def _put(user: SecondUser, **kwargs: Any):
    return display_picture_request(user, "PUT", **kwargs)


@pytest.mark.parametrize(
    ("fmt", "mime"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("JPEG", "image/jpg"),
        ("WEBP", "image/webp"),
        ("GIF", "image/gif"),
    ],
    ids=["png", "jpeg", "jpg", "webp", "gif"],
)
def test_upload_returns_the_stored_jpeg(no_picture: SecondUser, fmt: str, mime: str) -> None:
    resp = _put(no_picture, files={"file": (f"dp.{fmt.lower()}", image_bytes(fmt), mime)})
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers["Content-Type"].startswith("image/jpeg"), resp.headers
    assert resp.content.startswith(JPEG_MAGIC)

    (stored,) = read_display_pictures(no_picture.user_id)
    assert stored["mimeType"] == "image/jpeg", stored

    fetched = display_picture_request(no_picture, "GET")
    assert fetched.status_code == 200, fetched.text[:500]
    assert_strict_openapi_exchange(fetched, ROUTE)
    assert fetched.content == resp.content


def test_a_second_upload_replaces_the_first(no_picture: SecondUser) -> None:
    first = _put(no_picture, files={"file": ("a.png", TINY_PNG, "image/png")})
    assert first.status_code == 201, first.text[:500]
    second = _put(no_picture, files={"file": ("b.gif", image_bytes("GIF", (3, 3)), "image/gif")})
    assert second.status_code == 201, second.text[:500]
    assert_strict_openapi_exchange(second, ROUTE)
    assert len(read_display_pictures(no_picture.user_id)) == 1


def test_the_declared_type_is_trusted_not_the_bytes(no_picture: SecondUser) -> None:
    # The type check reads the part's Content-Type only; sharp then decodes whatever arrives.
    resp = _put(no_picture, files={"file": ("dp.gif", TINY_PNG, "image/gif")})
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_other_form_fields_are_ignored(no_picture: SecondUser) -> None:
    resp = _put(
        no_picture,
        files={"file": ("dp.png", TINY_PNG, "image/png")},
        data={"userId": "0123456789abcdef01234567"},
    )
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert len(read_display_pictures(no_picture.user_id)) == 1


def test_bytes_that_are_not_an_image_are_an_internal_error(no_picture: SecondUser) -> None:
    resp = _put(no_picture, files={"file": ("dp.png", b"not an image at all", "image/png")})
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert read_display_pictures(no_picture.user_id) == []


def test_an_image_still_over_100_kb_as_jpeg_is_too_large(no_picture: SecondUser) -> None:
    # About 10 KB as PNG, about 400 KB as JPEG even at quality 10.
    resp = _put(no_picture, files={"file": ("big.png", noisy_png(2048, 1024), "image/png")})
    assert resp.status_code == 413, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_PAYLOAD_TOO_LARGE", resp.text[:500]
    assert "File too large , limit:1MB" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert read_display_pictures(no_picture.user_id) == []


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"file": ("dp.txt", b"hello", "text/plain")}, "Invalid file type"),
        ({"file": ("dp.svg", b"<svg/>", "image/svg+xml")}, "Invalid file type"),
        ({"file": ("dp.png", b"\x00" * (1024 * 1024 + 1), "image/png")}, "larger than the 1 MB limit"),
        ({"picture": ("dp.png", TINY_PNG, "image/png")}, "weren't sent the way this page expects"),
        (
            [("file", ("a.png", TINY_PNG, "image/png")), ("file", ("b.png", TINY_PNG, "image/png"))],
            "You can upload up to 1 files at a time",
        ),
    ],
    ids=["text-file", "svg", "over-1-mb", "wrong-field-name", "two-files"],
)
def test_a_refused_upload_is_a_bad_request(
    no_picture: SecondUser, files: Any, message: str
) -> None:
    resp = _put(no_picture, files=files)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert message in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert read_display_pictures(no_picture.user_id) == []


def test_a_form_without_a_file_is_a_bad_request(no_picture: SecondUser) -> None:
    # requests only sends multipart when there is a file part, so build the form by hand.
    boundary = "specauditboundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nhi\r\n--{boundary}--\r\n"
    ).encode()
    resp = _put(
        no_picture,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code == 400, resp.text[:500]
    assert "no files were received" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_no_body_is_a_bad_request(no_picture: SecondUser) -> None:
    resp = _put(no_picture)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert "No files available for processing" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_a_json_body_is_a_bad_request(no_picture: SecondUser) -> None:
    with outside_request_contract("a JSON body where only multipart is documented"):
        resp = _put(no_picture, json={"file": "aGVsbG8="})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 400, resp.text[:500]
    assert "No files available for processing" in resp.text, resp.text[:500]


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    no_picture: SecondUser, headers: dict[str, str], message: str
) -> None:
    resp = requests.put(
        f"{no_picture.base_url}{ROUTE}",
        headers=headers,
        files={"file": ("dp.png", TINY_PNG, "image/png")},
        timeout=no_picture.timeout,
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
