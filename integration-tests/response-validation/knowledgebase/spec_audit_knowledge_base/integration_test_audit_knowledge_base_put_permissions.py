"""Strict OpenAPI audit of PUT /api/v1/knowledgeBase/:kbId/permissions.

authenticate -> requireScopes(kb:write) -> zod (kbId UUID; role OWNER/WRITER/READER required;
userIds and teamIds string arrays, optional, teamIds refused when not empty) ->
updateKBPermission, which reads userIds.length and teamIds.length and refuses two empty lists
-> connector service PUT /api/v1/kb/{kb_id}/permissions. Only an OWNER may change roles.
User ids are the Mongo user ids; the response answers with graph ids.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.kb_client import KBClient
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


def _role_of(kb_client: KBClient, kb_id: str, graph_id: str) -> str | None:
    resp = kb_client.get(_path(kb_id))
    assert resp.status_code == 200, resp.text[:500]
    return next((p["role"] for p in resp.json()["permissions"] if p["id"] == graph_id), None)


def _admin_mongo_id(kb_client: KBClient, kb_id: str) -> str:
    resp = kb_client.get(_path(kb_id))
    assert resp.status_code == 200, resp.text[:500]
    return str(next(p["userId"] for p in resp.json()["permissions"] if p["role"] == "OWNER"))


def test_update_changes_the_role_and_answers_with_graph_ids(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="READER")

    resp = kb_client.put(_path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "WRITER"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"kbId": kb_id, "userIds": [second_user.graph_id], "teamIds": [], "newRole": "WRITER"}
    assert _role_of(kb_client, kb_id, second_user.graph_id) == "WRITER"


def test_update_by_a_member_owner(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="OWNER")
    resp = request_as(
        second_user, "PUT", _path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "READER"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _role_of(kb_client, kb_id, second_user.graph_id) == "READER"


def test_update_ignores_other_fields_and_the_query(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="READER")
    with outside_request_contract("the validator drops body fields and query parameters it does not know"):
        resp = kb_client.put(
            _path(kb_id),
            params={"notify": "true"},
            json={"userIds": [second_user.user_id], "teamIds": [], "role": "WRITER", "expiresAt": 1},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _role_of(kb_client, kb_id, second_user.graph_id) == "WRITER"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"userIds": ["someone"], "teamIds": []}, id="role-missing"),
        pytest.param({"userIds": ["someone"], "teamIds": [], "role": "ADMIN"}, id="unknown-role"),
        pytest.param({"userIds": [], "teamIds": ["some-team"], "role": "READER"}, id="team-ids-not-empty"),
        pytest.param({"userIds": "someone", "teamIds": [], "role": "READER"}, id="user-ids-not-a-list"),
    ],
)
def test_update_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: dict[str, Any]) -> None:
    resp = kb_client.put(_path(MISSING_RECORD_ID), json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_with_two_empty_lists_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.put(_path(MISSING_RECORD_ID), json={"userIds": [], "teamIds": [], "role": "READER"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == "User IDs or team IDs are required"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"userIds": ["someone"], "role": "READER"}, id="team-ids-missing"),
        pytest.param({"teamIds": [], "role": "READER"}, id="user-ids-missing"),
    ],
)
def test_update_without_both_id_lists_is_an_internal_error(kb_client: KBClient, payload: dict[str, Any]) -> None:
    resp = kb_client.put(_path(MISSING_RECORD_ID), json=payload)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_INTERNAL_SERVER_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_with_a_graph_id_or_an_unknown_user_is_bad_request(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="READER")
    for user_id in (second_user.graph_id, MISSING_RECORD_ID):
        resp = kb_client.put(_path(kb_id), json={"userIds": [user_id], "teamIds": [], "role": "WRITER"})
        assert resp.status_code == 400, resp.text[:500]
        assert resp.json()["error"]["message"] == UNKNOWN_PEOPLE
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _role_of(kb_client, kb_id, second_user.graph_id) == "READER"


def test_update_a_user_without_a_permission_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    resp = kb_client.put(_path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "WRITER"})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "No users or teams with existing permissions found to update"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_that_would_leave_no_owner_is_bad_request(kb_client: KBClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    admin = _admin_mongo_id(kb_client, kb_id)
    resp = kb_client.put(_path(kb_id), json={"userIds": [admin], "teamIds": [], "role": "READER"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == (
        "Cannot remove all owners from the knowledge base. At least one owner must remain."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_several_users_including_an_owner_is_bad_request(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="OWNER")
    admin = _admin_mongo_id(kb_client, kb_id)
    resp = kb_client.put(
        _path(kb_id), json={"userIds": [second_user.user_id, admin], "teamIds": [], "role": "WRITER"}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == (
        "Cannot perform bulk operations on Owner permissions. Please update Owners one at a time."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_kb_id_that_is_not_a_uuid_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.put(_path(MALFORMED_ID), json={"userIds": ["someone"], "teamIds": [], "role": "READER"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "params.kbId"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_on_an_unknown_knowledge_base_is_not_found(kb_client: KBClient, second_user: SecondUser) -> None:
    resp = kb_client.put(
        _path(MISSING_RECORD_ID), json={"userIds": [second_user.user_id], "teamIds": [], "role": "READER"}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_by_a_member_without_a_role_is_not_found(second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    resp = request_as(
        second_user, "PUT", _path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "OWNER"}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("role", ["READER", "WRITER"])
def test_update_by_a_non_owner_is_forbidden(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb, role: str
) -> None:
    kb_id = make_kb(member_role=role)
    resp = request_as(
        second_user, "PUT", _path(kb_id), json={"userIds": [second_user.user_id], "teamIds": [], "role": "OWNER"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Only KB owners can update permissions"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _role_of(kb_client, kb_id, second_user.graph_id) == role


def test_update_kb_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.put(_path(UNSAFE_ID), json={"userIds": ["someone"], "teamIds": [], "role": "READER"})
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
    resp = kb_client.put(
        _path(MISSING_RECORD_ID), auth=False, headers=headers, json={"userIds": ["x"], "teamIds": [], "role": "READER"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_with_a_token_lacking_kb_write_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.put(
        _path(MISSING_RECORD_ID),
        auth=False,
        headers=unscoped_headers,
        json={"userIds": ["x"], "teamIds": [], "role": "READER"},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:write"
    assert_strict_openapi_exchange(resp, ROUTE)
