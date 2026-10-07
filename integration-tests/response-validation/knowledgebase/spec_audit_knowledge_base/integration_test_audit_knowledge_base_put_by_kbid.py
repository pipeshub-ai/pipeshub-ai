"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/:kbId.

body parser -> XSS check (refuses HTML, trims every string) -> authenticate ->
requireScopes(kb:write) -> zod (kbId UUID, kbName 1..255 optional) -> updateKnowledgeBase,
which refuses format specifiers in the name and sends only the name to the connector
service (PUT /api/v1/kb/{kb_id}). The connector refuses a body with nothing to update.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    HTML_REFUSED_MESSAGE,
    MALFORMED_ID,
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    MakeKb,
    request_as,
    unique_name,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId"
UPDATED = {"success": True, "message": "Knowledge base updated successfully"}
NOTHING_TO_UPDATE = "No fields to update"
MAX_NAME_LENGTH = 255


def _name_of(kb_client: KBClient, kb_id: str) -> str:
    resp = kb_client.get(f"/{kb_id}")
    assert resp.status_code == 200, resp.text[:500]
    return str(resp.json()["name"])


def test_rename_answers_success_and_stores_the_trimmed_name(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    name = unique_name("spec-audit-renamed")

    resp = kb_client.put(f"/{kb_id}", json={"kbName": f"  {name}\t"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED
    assert _name_of(kb_client, kb_id) == name


def test_rename_accepts_a_name_at_the_length_limit(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    name = unique_name("k").ljust(MAX_NAME_LENGTH, "k")
    resp = kb_client.put(f"/{kb_id}", json={"kbName": name})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id) == name


def test_rename_as_writer_is_allowed(second_user: SecondUser, kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="WRITER")
    name = unique_name("spec-audit-writer")
    resp = request_as(second_user, "PUT", f"/{kb_id}", json={"kbName": name})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id) == name


def test_rename_ignores_other_fields_and_the_query(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    name = unique_name("spec-audit-extra")
    with outside_request_contract("the validator drops body fields and query parameters it does not know"):
        resp = kb_client.put(f"/{kb_id}", params={"notify": "true"}, json={"kbName": name, "isHidden": True})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id) == name


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-object"),
        pytest.param(None, id="no-body"),
    ],
)
def test_update_with_nothing_to_change_is_bad_request(kb_client: KBClient, make_kb: MakeKb, payload: Any) -> None:
    kb_id = make_kb()
    resp = kb_client.put(f"/{kb_id}", json=payload) if payload is not None else kb_client.put(f"/{kb_id}")
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert error["message"] == NOTHING_TO_UPDATE
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_with_only_unknown_fields_is_bad_request(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    with outside_request_contract("an unknown field is dropped, which leaves nothing to update"):
        resp = kb_client.put(f"/{kb_id}", json={"name": unique_name()})
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == NOTHING_TO_UPDATE


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"kbName": None}, id="name-null"),
        pytest.param({"kbName": ""}, id="name-empty"),
        pytest.param({"kbName": " \t "}, id="name-only-whitespace"),
        pytest.param({"kbName": "k" * (MAX_NAME_LENGTH + 1)}, id="name-too-long"),
        pytest.param({"kbName": 20261006}, id="name-not-a-string"),
    ],
)
def test_update_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: Any) -> None:
    # Refused by the gateway, so the knowledge base never has to exist.
    resp = kb_client.put(f"/{MISSING_RECORD_ID}", json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_kb_id_that_is_not_a_uuid_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/{MALFORMED_ID}", json={"kbName": unique_name()})
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["metadata"]["errors"][0]["field"] == "params.kbId"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("name", ["spec-audit %s", "spec-audit %n", "spec-audit %08x"])
def test_update_refuses_format_specifiers_in_the_name(kb_client: KBClient, make_kb: MakeKb, name: str) -> None:
    kb_id = make_kb()
    resp = kb_client.put(f"/{kb_id}", json={"kbName": name})
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert "format specifiers" in error["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_accepts_a_percent_sign_that_is_not_a_format_specifier(
    kb_client: KBClient, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    name = f"{unique_name()} 50% off"
    resp = kb_client.put(f"/{kb_id}", json={"kbName": name})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id) == name


def test_update_refuses_html_in_the_name_before_the_token_check(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/{MISSING_RECORD_ID}", auth=False, json={"kbName": "<b>spec-audit</b>"})
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert HTML_REFUSED_MESSAGE in error["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_unknown_knowledge_base_is_not_found(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/{MISSING_RECORD_ID}", json={"kbName": unique_name()})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_as_member_without_a_role_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    before = _name_of(kb_client, kb_id)
    resp = request_as(second_user, "PUT", f"/{kb_id}", json={"kbName": unique_name()})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _name_of(kb_client, kb_id) == before


def test_update_as_reader_is_forbidden(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="READER")
    resp = request_as(second_user, "PUT", f"/{kb_id}", json={"kbName": unique_name()})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == (
        "You do not have permission to perform this action on this knowledge base"
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.put(f"/{UNSAFE_ID}", json={"kbName": unique_name()})
    assert resp.status_code == 400, resp.text[:500]
    assert UNSAFE_ID_MESSAGE in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_update_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.put(f"/{MISSING_RECORD_ID}", auth=False, headers=headers, json={"kbName": unique_name()})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.put(f"/{MISSING_RECORD_ID}", auth=False, headers=unscoped_headers, json={"kbName": unique_name()})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
