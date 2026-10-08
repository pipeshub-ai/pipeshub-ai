"""Strict OpenAPI audit of POST /api/v1/users/bulk/invite/upload.

Successful imports run as ``upload_member``: the import reports to its uploader
through a notification, and that member is removed with its notifications.
"""

from __future__ import annotations

import io
import json
import time
from typing import Any, Callable

import pytest
import requests
from helper import mailpit
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    MISSING_USER_ID,
    USERS_BASE,
    csv_file,
    users_with_emails,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/bulk/invite/upload"
STARTED = {"message": "Import started. You will be notified when it finishes."}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

NewAddress = Callable[[], str]


def _upload(base_url: str, token: str | None, **kwargs: Any) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    headers.update(kwargs.pop("headers", {}))
    return requests.post(f"{base_url}{USERS_BASE}/bulk/invite/upload", headers=headers, timeout=60, **kwargs)


def _as_admin(client: PipeshubClient, **kwargs: Any) -> requests.Response:
    token = client._headers()["Authorization"].removeprefix("Bearer ")
    return _upload(client.base_url, token, **kwargs)


def _as(user: SecondUser, **kwargs: Any) -> requests.Response:
    return _upload(user.base_url, user.token, **kwargs)


def _wait_for_account(address: str, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = users_with_emails([address])
        if found:
            return found[0]
        time.sleep(1)
    raise AssertionError(f"the background import created no account for {address}")


def _xlsx(*cells: str) -> bytes:
    from openpyxl import Workbook  # noqa: PLC0415 - only this case needs it

    book = Workbook()
    for cell in cells:
        book.active.append([cell])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


@pytest.mark.parametrize("kind", ["csv", "xlsx", "octet-stream"])
def test_a_member_imports_a_file_and_the_invites_run_in_the_background(
    upload_member: SecondUser, second_user: SecondUser, invitee: NewAddress, kind: str
) -> None:
    address = invitee()
    seen = mailpit.message_ids(address)
    cells = ("email", address.upper(), second_user.email, "not-an-email@")
    files = {
        "csv": csv_file(*cells),
        "xlsx": {"file": ("invite.xlsx", _xlsx(*cells), XLSX)},
        "octet-stream": {"file": ("invite.csv", "\n".join(cells).encode(), "application/octet-stream")},
    }[kind]
    resp = _as(upload_member, files=files)
    assert resp.status_code == 202, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == STARTED
    user = _wait_for_account(address)
    assert (user["email"], user["role"], user["hasLoggedIn"]) == (address, "member", False), user
    mailpit.wait_for_new_message(address, "You are invited to join", seen)


def test_group_ids_that_are_not_json_are_ignored(
    upload_member: SecondUser, second_user: SecondUser
) -> None:
    # A non-JSON groupIds would be refused for a member if it were read; it is dropped instead.
    resp = _as(upload_member, files=csv_file(second_user.email), data={"groupIds": "not json"})
    assert resp.status_code == 202, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_other_form_fields_are_ignored(upload_member: SecondUser, second_user: SecondUser) -> None:
    resp = _as(upload_member, files=csv_file(second_user.email), data={"role": "admin"})
    assert resp.status_code == 202, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_member_assigning_groups_is_forbidden(
    upload_member: SecondUser, second_user: SecondUser
) -> None:
    resp = _as(
        upload_member, files=csv_file(second_user.email), data={"groupIds": json.dumps([MISSING_USER_ID])}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Members cannot assign groups when inviting" in resp.text, resp.text[:500]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"files": {"note": (None, "x")}}, "File upload required but no files were received"),
        ({"files": {"file": ("a.csv", b"", "text/csv")}}, "No email addresses found in the file"),
        ({"files": csv_file("name", "bob")}, "No email addresses found in the file"),
        ({"files": {"file": ("a.txt", b"a@example.com\n", "text/plain")}}, "Invalid file type"),
        ({"files": {"file": ("a.xlsx", b"PK\x03\x04garbage", XLSX)}}, "Could not read the file"),
        (
            {"files": csv_file(*(f"spec-audit-{i}@example.com" for i in range(1001)))},
            "File exceeds the 1000-user limit",
        ),
        (
            {"files": csv_file("spec-audit@example.com"), "data": {"groupIds": '["not-a-group"]'}},
            "groupIds must contain valid MongoDB ObjectIds",
        ),
        (
            {"files": {"file": ("a.csv", b"a@example.com," + b"x" * (5 * 1024 * 1024), "text/csv")}},
            "larger than the 5 MB limit",
        ),
        (
            {"files": [("file", ("a.csv", b"a@example.com\n", "text/csv")), ("file", ("b.csv", b"b@example.com\n", "text/csv"))]},
            "You can upload up to 1 files at a time",
        ),
        ({"files": {"upload": ("a.csv", b"a@example.com\n", "text/csv")}}, "weren't sent the way this page expects"),
    ],
    ids=[
        "no-file-part",
        "empty-file",
        "no-addresses",
        "wrong-type",
        "unreadable-xlsx",
        "over-1000",
        "bad-group-id",
        "over-5-mb",
        "two-files",
        "wrong-field-name",
    ],
)
def test_an_unusable_upload_is_a_bad_request(
    pipeshub_client: PipeshubClient, kwargs: dict[str, Any], message: str
) -> None:
    resp = _as_admin(pipeshub_client, **kwargs)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]


def test_a_json_body_is_a_bad_request(pipeshub_client: PipeshubClient) -> None:
    with outside_request_contract("a JSON body where the route takes multipart"):
        resp = _as_admin(pipeshub_client, json={"file": "spec-audit@example.com"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 400, resp.text[:500]
    assert "No files available for processing" in resp.text, resp.text[:500]


@pytest.mark.parametrize(
    ("token", "message"),
    [(None, "No token provided"), ("not-a-jwt", "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    pipeshub_client: PipeshubClient, token: str | None, message: str
) -> None:
    resp = _upload(pipeshub_client.base_url, token, files=csv_file("spec-audit@example.com"))
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]

