"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/:kbId/permissions.

guardPathParams(kbId) -> authenticate -> requireScopes(kb:read) -> zod (kbId non-empty) ->
listKBPermissions -> connector service GET /api/v1/kb/{kb_id}/permissions. Only an OWNER may
see the list.
"""

from __future__ import annotations

import pytest
from helper.clients.kb_client import KBClient
from helper.kb_sharing import all_team_id, grant
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from knowledge_base_audit_support import (
    MALFORMED_ID,
    MISSING_RECORD_ID,
    UNSAFE_ID,
    UNSAFE_ID_MESSAGE,
    MakeKb,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/permissions"
ENTRY_FIELDS = {"id", "userId", "email", "name", "role", "type", "createdAtTimestamp", "updatedAtTimestamp"}


def _path(kb_id: str) -> str:
    return f"/{kb_id}/permissions"


def test_list_shows_users_and_teams(
    kb_client: KBClient, pipeshub_client: PipeshubClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="WRITER")
    team_id = all_team_id(pipeshub_client.org_id)
    grant(pipeshub_client, kb_id, team_ids=[team_id])

    resp = kb_client.get(_path(kb_id))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"kbId", "permissions", "totalCount"}
    assert body["kbId"] == kb_id
    assert body["totalCount"] == len(body["permissions"]) == 3
    entries = {p["id"]: p for p in body["permissions"]}
    assert all(set(p) == ENTRY_FIELDS for p in body["permissions"]), body
    member = entries[second_user.graph_id]
    assert (member["type"], member["role"], member["userId"]) == ("USER", "WRITER", second_user.user_id)
    team = entries[team_id]
    assert (team["type"], team["role"], team["userId"], team["email"]) == ("TEAM", None, None, None)
    owners = [p for p in body["permissions"] if p["role"] == "OWNER"]
    assert len(owners) == 1 and owners[0]["type"] == "USER"


def test_list_by_a_member_owner(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="OWNER")
    resp = request_as(second_user, "GET", _path(kb_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["totalCount"] == 2


def test_list_ignores_the_query(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    with outside_request_contract("the validator checks only the kbId path parameter"):
        resp = kb_client.get(_path(kb_id), params={"type": "TEAM"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["totalCount"] == 1


@pytest.mark.parametrize("role", ["READER", "WRITER"])
def test_list_by_a_non_owner_is_forbidden(second_user: SecondUser, make_kb: MakeKb, role: str) -> None:
    kb_id = make_kb(member_role=role)
    resp = request_as(second_user, "GET", _path(kb_id))
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Only owners can view the knowledge base sharing list"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_by_a_member_without_a_role_is_not_found(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    resp = request_as(second_user, "GET", _path(kb_id))
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("kb_id", [MISSING_RECORD_ID, MALFORMED_ID], ids=["unknown-uuid", "not-a-uuid"])
def test_list_of_an_unknown_knowledge_base_is_not_found(kb_client: KBClient, kb_id: str) -> None:
    resp = kb_client.get(_path(kb_id))
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_kb_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.get(_path(UNSAFE_ID))
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
def test_list_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.get(_path(MISSING_RECORD_ID), auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_with_a_token_lacking_kb_read_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.get(_path(MISSING_RECORD_ID), auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:read"
    assert_strict_openapi_exchange(resp, ROUTE)
