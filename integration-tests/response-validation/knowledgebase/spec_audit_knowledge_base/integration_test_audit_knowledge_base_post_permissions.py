"""Strict OpenAPI audit of POST /api/v1/knowledgeBase/:kbId/permissions.

authenticate -> requireScopes(kb:write) -> zod (kbId UUID; userIds, teamIds string arrays and
role OWNER/WRITER/READER, all optional, with two refinements: at least one id, and a role
when userIds is non-empty) -> createKBPermission, which reads userIds.length and
teamIds.length -> connector service POST /api/v1/kb/{kb_id}/permissions. Only an OWNER may grant.
User ids are the Mongo user ids; team ids are graph team ids.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
from helper.kb_sharing import all_team_id
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
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/permissions"
UNKNOWN_PEOPLE = "Some people you picked are no longer in this workspace. Remove them and try sharing again."


def _path(kb_id: str) -> str:
    return f"/{kb_id}/permissions"


def _entries(kb_client: KBClient, kb_id: str) -> dict[str, dict[str, Any]]:
    resp = kb_client.get(_path(kb_id))
    assert resp.status_code == 200, resp.text[:500]
    return {p["id"]: p for p in resp.json()["permissions"]}


def test_grant_a_user_answers_201_with_the_graph_id(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()

    resp = kb_client.post(_path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "WRITER"})

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "kbId": kb_id,
        "permissionResult": {
            "success": True,
            "grantedCount": 1,
            "grantedUsers": [second_user.graph_id],
            "grantedTeams": [],
            "role": "WRITER",
            "kbId": kb_id,
            "details": {},
        },
    }
    assert _entries(kb_client, kb_id)[second_user.graph_id]["role"] == "WRITER"
    member_view = request_as(second_user, "GET", f"/{kb_id}")
    assert member_view.json()["userRole"] == "WRITER"


def test_grant_again_changes_the_role_and_counts_a_repeated_id_once(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="READER")
    resp = kb_client.post(
        _path(kb_id), json={"userIds": [second_user.user_id, second_user.user_id], "teamIds": [], "role": "OWNER"}
    )
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["permissionResult"]["grantedCount"] == 1
    assert _entries(kb_client, kb_id)[second_user.graph_id]["role"] == "OWNER"


def test_grant_a_team_gives_its_members_read_access_whatever_the_role(
    kb_client: KBClient, pipeshub_client: PipeshubClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    team_id = all_team_id(pipeshub_client.org_id)

    resp = kb_client.post(_path(kb_id), json={"userIds": [], "teamIds": [team_id], "role": "WRITER"})

    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    result = resp.json()["permissionResult"]
    assert result["grantedTeams"] == [team_id]
    assert result["grantedUsers"] == []
    # The role is echoed, but the team is stored without one and its members read as READER.
    assert result["role"] == "WRITER"
    assert _entries(kb_client, kb_id)[team_id]["role"] is None
    assert request_as(second_user, "GET", f"/{kb_id}").json()["userRole"] == "READER"


def test_grant_a_team_without_a_role(kb_client: KBClient, pipeshub_client: PipeshubClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    team_id = all_team_id(pipeshub_client.org_id)
    resp = kb_client.post(_path(kb_id), json={"userIds": [], "teamIds": [team_id]})
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["permissionResult"]["role"] == "READER"


def test_grant_ignores_other_fields_and_the_query(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    with outside_request_contract("the validator drops body fields and query parameters it does not know"):
        resp = kb_client.post(
            _path(kb_id),
            params={"notify": "true"},
            json={"userIds": [second_user.user_id], "teamIds": [], "role": "READER", "expiresAt": 1},
        )
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no-ids"),
        pytest.param({"userIds": [], "teamIds": []}, id="both-empty"),
        pytest.param({"userIds": ["someone"], "teamIds": []}, id="user-without-role"),
        pytest.param({"userIds": ["someone"], "teamIds": [], "role": "ADMIN"}, id="unknown-role"),
        pytest.param({"userIds": "someone", "teamIds": [], "role": "READER"}, id="user-ids-not-a-list"),
        pytest.param({"userIds": [7], "teamIds": [], "role": "READER"}, id="user-id-not-a-string"),
    ],
)
def test_grant_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: dict[str, Any]) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_ID), json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"userIds": ["someone"], "role": "READER"}, id="team-ids-missing"),
        pytest.param({"teamIds": ["some-team"]}, id="user-ids-missing"),
    ],
)
def test_grant_without_both_id_lists_is_an_internal_error(
    kb_client: KBClient, make_kb: MakeKb, payload: dict[str, Any]
) -> None:
    # The handler reads .length on both lists before checking them.
    kb_id = make_kb()
    resp = kb_client.post(_path(kb_id), json=payload)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_INTERNAL_SERVER_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_grant_to_a_graph_id_or_an_unknown_user_is_bad_request(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    for user_id in (second_user.graph_id, MISSING_RECORD_ID):
        resp = kb_client.post(_path(kb_id), json={"userIds": [user_id], "teamIds": [], "role": "READER"})
        assert resp.status_code == 400, resp.text[:500]
        assert resp.json()["error"]["message"] == UNKNOWN_PEOPLE
        assert_strict_openapi_exchange(resp, ROUTE)
    assert set(_entries(kb_client, kb_id)) != {second_user.graph_id}


def test_grant_kb_id_that_is_not_a_uuid_is_rejected(kb_client: KBClient, second_user: SecondUser) -> None:
    resp = kb_client.post(_path(MALFORMED_ID), json={"userIds": [second_user.user_id], "teamIds": [], "role": "READER"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "params.kbId"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_grant_on_an_unknown_knowledge_base_is_not_found(kb_client: KBClient, second_user: SecondUser) -> None:
    resp = kb_client.post(
        _path(MISSING_RECORD_ID), json={"userIds": [second_user.user_id], "teamIds": [], "role": "READER"}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_grant_by_a_member_without_a_role_is_not_found(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    resp = request_as(
        second_user, "POST", _path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "OWNER"}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("role", ["READER", "WRITER"])
def test_grant_by_a_non_owner_is_forbidden(second_user: SecondUser, make_kb: MakeKb, role: str) -> None:
    kb_id = make_kb(member_role=role)
    resp = request_as(
        second_user, "POST", _path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "OWNER"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Only KB owners can grant permissions"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_grant_kb_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.post(_path(UNSAFE_ID), json={"userIds": [], "teamIds": ["x"]})
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
def test_grant_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.post(_path(MISSING_RECORD_ID), auth=False, headers=headers, json={"userIds": [], "teamIds": ["x"]})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_grant_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.post(
        _path(MISSING_RECORD_ID), auth=False, headers=unscoped_headers, json={"userIds": [], "teamIds": ["x"]}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
