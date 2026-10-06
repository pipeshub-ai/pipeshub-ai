"""Strict OpenAPI audit of PUT /api/v1/org/logo.

The upload middleware runs before the admin check, so a request without a usable
file is a 400 for any caller, admin or not.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser
from org_audit_support import (
    HANDLER_SVG,
    INVALID_BEARER,
    LOGO_ROUTE,
    SAFE_SVG,
    SCRIPT_SVG,
    TINY_PNG,
    ScopedCaller,
    error_of,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

MAX_LOGO_BYTES = 2 * 1024 * 1024
JPEG_MAGIC = b"\xff\xd8\xff"


def _upload(org_client: OrgClient, **kwargs: Any) -> Any:
    return org_client.put("/logo", **kwargs)


@pytest.mark.parametrize(
    ("filename", "content", "mime", "stored_mime"),
    [
        pytest.param("logo.png", TINY_PNG, "image/png", "image/jpeg", id="png-becomes-jpeg"),
        pytest.param("logo.svg", SAFE_SVG, "image/svg+xml", "image/svg+xml", id="svg-kept-as-is"),
    ],
)
def test_admin_uploads_a_logo(
    org_client: OrgClient, logo_restored: str, filename: str, content: bytes, mime: str, stored_mime: str
) -> None:
    resp = _upload(org_client, files={"file": (filename, content, mime)})
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert resp.json() == {"message": "Logo updated successfully", "mimeType": stored_mime}

    stored = org_client.get_logo()
    assert stored.status_code == 200, stored.text[:300]
    assert_strict_openapi_exchange(stored, LOGO_ROUTE)
    assert stored.headers["Content-Type"].startswith(stored_mime)
    if stored_mime == "image/jpeg":
        assert stored.content.startswith(JPEG_MAGIC)
    else:
        assert stored.content == content


@pytest.mark.parametrize(
    ("files", "message_part"),
    [
        pytest.param({"file": ("logo.svg", SCRIPT_SVG, "image/svg+xml")}, "script tags", id="svg-with-script"),
        pytest.param({"file": ("logo.svg", HANDLER_SVG, "image/svg+xml")}, "event handlers", id="svg-with-handler"),
        pytest.param(
            {"file": ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:x()"/></svg>', "image/svg+xml")},
            "javascript:",
            id="svg-with-javascript-url",
        ),
        pytest.param(
            {"file": ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><iframe src="x"></iframe></svg>', "image/svg+xml")},
            "iframe, object, or embed",
            id="svg-with-iframe",
        ),
        pytest.param({"file": ("logo.txt", b"not an image", "text/plain")}, "Invalid file type", id="text-file"),
        pytest.param({"logo": ("logo.png", TINY_PNG, "image/png")}, "weren't sent the way", id="wrong-field-name"),
        pytest.param(
            {"file": ("big.png", b"\0" * (MAX_LOGO_BYTES + 1), "image/png")}, "larger than the 2 MB", id="over-2mb"
        ),
    ],
)
def test_bad_upload_is_refused(
    org_client: OrgClient, logo_restored: str, files: dict[str, Any], message_part: str
) -> None:
    resp = _upload(org_client, files=files)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert message_part in error_of(resp)["message"]


def test_two_files_are_refused(org_client: OrgClient, logo_restored: str) -> None:
    files = [("file", ("a.png", TINY_PNG, "image/png")), ("file", ("b.png", TINY_PNG, "image/png"))]
    resp = _upload(org_client, files=files)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_multipart_without_a_file_is_refused(org_client: OrgClient, logo_restored: str) -> None:
    resp = _upload(org_client, files={"note": (None, "no file here")})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert error_of(resp)["message"] == "File upload required but no files were received"


def test_json_body_instead_of_multipart_is_refused(org_client: OrgClient, logo_restored: str) -> None:
    resp = _upload(org_client, json={"file": "aGVsbG8="})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert error_of(resp)["message"] == "No files available for processing"


def test_raster_that_does_not_decode_is_an_internal_error(org_client: OrgClient, logo_restored: str) -> None:
    resp = _upload(org_client, files={"file": ("logo.png", b"not really a png", "image/png")})
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert error_of(resp)["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(
    org_client: OrgClient, logo_restored: str, headers: dict[str, str]
) -> None:
    resp = _upload(org_client, auth=False, headers=headers, files={"file": ("logo.png", TINY_PNG, "image/png")})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_member_with_a_file_is_forbidden(second_user: SecondUser, logo_restored: str) -> None:
    resp = request_as(second_user, "PUT", "/logo", files={"file": ("logo.png", TINY_PNG, "image/png")})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_member_without_a_file_gets_the_upload_error_first(second_user: SecondUser, logo_restored: str) -> None:
    resp = request_as(second_user, "PUT", "/logo", files={"note": (None, "x")})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)


def test_oauth_token_without_org_write_is_forbidden(narrow_scope: ScopedCaller, logo_restored: str) -> None:
    resp = narrow_scope("PUT", "/logo", files={"file": ("logo.png", TINY_PNG, "image/png")})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, LOGO_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:write"
