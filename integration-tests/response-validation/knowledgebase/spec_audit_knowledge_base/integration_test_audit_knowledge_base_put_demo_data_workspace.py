"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/demo-data/workspace.

authenticate -> KB_WRITE scope -> strict zod body ({enabled: boolean}) -> Node admin gate
-> sample accounts' sign-in switched in MongoDB -> connector service
(PUT /api/v1/demo-data/workspace). The setting is org-wide: every success case here puts
back what it found.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import DEMO_STATUS_FIELDS, request_as
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/demo-data/workspace"
PATH = "/demo-data/workspace"
STATUS_PATH = "/demo-data/status"


def _status(kb_client: KBClient) -> dict[str, Any]:
    resp = kb_client.get(STATUS_PATH)
    assert resp.status_code == 200, resp.text[:500]
    status: dict[str, Any] = resp.json()
    return status


def test_admin_saving_the_current_setting_answers_the_status(kb_client: KBClient) -> None:
    before = _status(kb_client)

    resp = kb_client.put(PATH, json={"enabled": not before["offForEveryone"]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(resp.json()) == DEMO_STATUS_FIELDS
    assert resp.json() == before


def test_admin_turns_the_demo_off_and_back_on(kb_client: KBClient, second_user: SecondUser) -> None:
    before = _status(kb_client)
    if before["hasDemo"] or before["offForEveryone"]:
        # Turning it off would hide the demo data from every other suite's search and chat;
        # saving the current value (the test above) is the success path this org allows.
        pytest.fail(
            "this org has the Acme Corp demo data (or has it switched off), so the switch cannot be "
            "flipped without changing what other running suites see"
        )

    try:
        off = kb_client.put(PATH, json={"enabled": False})
        assert off.status_code == 200, off.text[:500]
        assert_strict_openapi_exchange(off, ROUTE)
        # The answer is the status read before the save, with only this flag changed.
        assert off.json() == {**before, "offForEveryone": True}
        assert _status(kb_client)["offForEveryone"] is True
        # Org-wide: the member sees it too.
        member = request_as(second_user, "GET", STATUS_PATH)
        assert member.status_code == 200, member.text[:500]
        assert member.json()["offForEveryone"] is True
    finally:
        on = kb_client.put(PATH, json={"enabled": True})
    assert on.status_code == 200, on.text[:500]
    assert_strict_openapi_exchange(on, ROUTE)
    assert on.json()["offForEveryone"] is False
    assert _status(kb_client) == before


def test_workspace_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", PATH, json={"enabled": False})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == "Only admins can change this for everyone"


def test_workspace_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.put(PATH, auth=False, headers=unscoped_headers, json={"enabled": True})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)


# The body is validated before the admin gate, so these never reach the handler.
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="enabled-missing"),
        pytest.param({"enabled": None}, id="enabled-null"),
        pytest.param({"enabled": "false"}, id="enabled-not-boolean"),
        pytest.param({"enabled": True, "orgId": "other-org"}, id="unknown-key"),
        pytest.param([False], id="body-is-a-list"),
    ],
)
def test_workspace_invalid_body_is_rejected(kb_client: KBClient, body: Any) -> None:
    resp = kb_client.put(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_workspace_invalid_body_from_a_member_is_rejected_before_the_admin_gate(
    second_user: SecondUser,
) -> None:
    resp = request_as(second_user, "PUT", PATH, json={})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_workspace_missing_body_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.put(PATH)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_workspace_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.put(PATH, auth=False, headers=headers, json={"enabled": True})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
