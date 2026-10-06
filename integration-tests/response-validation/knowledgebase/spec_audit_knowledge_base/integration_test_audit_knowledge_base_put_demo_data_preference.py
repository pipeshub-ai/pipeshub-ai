"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/demo-data/preference.

authenticate -> requireScopes(kb:write) -> strict zod body ({include: boolean | null}) ->
setDemoDataPreference -> connector service (PUT /api/v1/demo-data/preference).
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import DEMO_STATUS_FIELDS, request_as
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/demo-data/preference"
PATH = "/demo-data/preference"
STATUS_PATH = "/demo-data/status"


def _expected_chosen(body: dict[str, Any], include: bool | None) -> bool | None:
    # The connector service reports no choice at all when the org has no demo data.
    return include if body["hasDemo"] else None


@pytest.mark.parametrize("include", [True, False, None], ids=["show", "hide", "back-to-default"])
def test_admin_sets_preference_and_gets_status_back(kb_client: KBClient, include: bool | None) -> None:
    before = kb_client.get(STATUS_PATH)
    assert before.status_code == 200, before.text[:500]
    original = before.json()["chosen"]

    try:
        resp = kb_client.put(PATH, json={"include": include})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        assert set(body) == DEMO_STATUS_FIELDS
        assert body["chosen"] is _expected_chosen(body, include)
        # Everything but the caller's own choice is as it was.
        for org_wide in ("hasDemo", "realData", "offForEveryone", "demoConnectorIds"):
            assert body[org_wide] == before.json()[org_wide]
    finally:
        kb_client.put(PATH, json={"include": original})


def test_member_resets_own_preference_with_null(second_user: SecondUser) -> None:
    before = request_as(second_user, "GET", STATUS_PATH)
    assert before.status_code == 200, before.text[:500]
    original = before.json()["chosen"]

    try:
        chosen = request_as(second_user, "PUT", PATH, json={"include": True})
        assert chosen.status_code == 200, chosen.text[:500]
        assert_strict_openapi_exchange(chosen, ROUTE)

        resp = request_as(second_user, "PUT", PATH, json={"include": None})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        assert set(body) == DEMO_STATUS_FIELDS
        assert body["chosen"] is None
    finally:
        request_as(second_user, "PUT", PATH, json={"include": original})


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="include-missing"),
        pytest.param({"include": "yes"}, id="include-not-boolean"),
        pytest.param({"include": 1}, id="include-a-number"),
        pytest.param({"include": True, "everyone": True}, id="unknown-field"),
        pytest.param([True], id="body-is-a-list"),
    ],
)
def test_invalid_body_is_rejected_by_the_gateway(kb_client: KBClient, payload: Any) -> None:
    resp = kb_client.put(PATH, json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_missing_body_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.put(PATH)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_preference_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.put(PATH, auth=False, headers=headers, json={"include": True})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_preference_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.put(PATH, auth=False, headers=unscoped_headers, json={"include": True})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
