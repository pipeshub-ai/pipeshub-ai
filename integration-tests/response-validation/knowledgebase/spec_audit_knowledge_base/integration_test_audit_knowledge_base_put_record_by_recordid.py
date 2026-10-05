"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/record/:recordId.

authenticate -> kb:write scope -> multer (optional ``file``) -> XSS check on
``recordName`` -> zod -> updateRecord, which proxies to the connector service
(PUT /api/v1/kb/record/{record_id}). Only the rename path is exercised: a new
file is stored and reindexed, which costs embedding work.
"""

from __future__ import annotations

import pytest
from knowledge_base_audit_support import (
    MISSING_RECORD_ID,
    SeedRecord,
    request_as,
    unique_name,
)
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/record/:recordId"

MultipartFields = dict[str, tuple[None, str]]


def rename_form(record_name: str) -> MultipartFields:
    """A multipart body with only the text field; the route reads fields through multer."""
    return {"recordName": (None, record_name)}


def test_rename_returns_updated_record(
    kb_client: KBClient, seed_record: SeedRecord
) -> None:
    record_id = seed_record()
    new_name = unique_name("spec-audit-renamed")

    resp = kb_client.put(f"/record/{record_id}", files=rename_form(new_name))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["message"] == "Record updated successfully"
    assert body["fileUploaded"] is False
    assert body["record"]["recordName"] == new_name


@pytest.mark.parametrize(
    "record_name",
    [
        pytest.param("<script>alert(1)</script>", id="html-in-name"),
        # Passes the gateway (non-empty string); the connector service strips it and refuses.
        pytest.param("   ", id="blank-name"),
    ],
)
def test_rejected_record_name_is_bad_request(
    kb_client: KBClient, seed_record: SeedRecord, record_name: str
) -> None:
    record_id = seed_record()

    resp = kb_client.put(f"/record/{record_id}", files=rename_form(record_name))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_without_kb_role_gets_not_found(
    second_user: SecondUser, seed_record: SeedRecord
) -> None:
    record_id = seed_record()

    # No role on the knowledge base hides the record instead of answering 403.
    resp = request_as(
        second_user,
        "PUT",
        f"/record/{record_id}",
        files=rename_form(unique_name("spec-audit-denied")),
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_put_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.put(
        f"/record/{MISSING_RECORD_ID}",
        auth=False,
        files=rename_form(unique_name()),
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
