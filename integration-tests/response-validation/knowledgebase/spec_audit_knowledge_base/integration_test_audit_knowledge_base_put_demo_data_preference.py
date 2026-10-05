"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/demo-data/preference."""

from __future__ import annotations

from typing import Any

import pytest
from knowledge_base_audit_support import DEMO_STATUS_FIELDS, request_as
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/demo-data/preference"
PATH = "/demo-data/preference"
STATUS_PATH = "/demo-data/status"


def _expected_chosen(body: dict[str, Any], include: bool | None) -> bool | None:
    # The connector service reports no choice at all when the org has no demo data.
    return include if body["hasDemo"] else None


def test_admin_sets_preference_and_gets_status_back(kb_client: KBClient) -> None:
    before = kb_client.get(STATUS_PATH)
    assert before.status_code == 200, before.text[:500]
    original = before.json()["chosen"]

    try:
        resp = kb_client.put(PATH, json={"include": False})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        body = resp.json()
        assert set(body) == DEMO_STATUS_FIELDS
        assert body["chosen"] is _expected_chosen(body, False)
    finally:
        kb_client.put(PATH, json={"include": original})


def test_member_resets_own_preference_with_null(second_user: SecondUser) -> None:
    before = request_as(second_user, "GET", STATUS_PATH)
    assert before.status_code == 200, before.text[:500]
    original = before.json()["chosen"]

    try:
        chosen = request_as(second_user, "PUT", PATH, json={"include": True})
        assert chosen.status_code == 200, chosen.text[:500]

        resp = request_as(second_user, "PUT", PATH, json={"include": None})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        body = resp.json()
        assert set(body) == DEMO_STATUS_FIELDS
        assert body["chosen"] is None
    finally:
        request_as(second_user, "PUT", PATH, json={"include": original})


def test_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.put(PATH, auth=False, json={"include": True})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"include": "yes"}, id="include-not-boolean"),
        pytest.param({"include": True, "everyone": True}, id="unknown-field"),
    ],
)
def test_invalid_body_is_rejected_by_the_gateway(
    kb_client: KBClient, payload: dict[str, Any]
) -> None:
    resp = kb_client.put(PATH, json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
