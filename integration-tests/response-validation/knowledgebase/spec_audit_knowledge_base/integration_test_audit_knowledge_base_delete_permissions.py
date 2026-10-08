"""Strict OpenAPI audit of DELETE /api/v1/knowledgeBase/:kbId/permissions.

authenticate -> requireScopes(kb:delete) -> zod (kbId UUID; userIds and teamIds string arrays,
optional) -> removeKBPermission, which reads userIds.length and teamIds.length and refuses two
empty lists -> connector service DELETE /api/v1/kb/{kb_id}/permissions. Only an OWNER may
remove access, and never the creator's. User ids are the Mongo user ids.
"""

from __future__ import annotations

from typing import Any

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
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/:kbId/permissions"
UNKNOWN_PEOPLE = "Some people you picked are no longer in this workspace. Remove them and try sharing again."
NOTHING_TO_REMOVE = "No users or teams with existing permissions found to remove"
CREATOR_STAYS = "Cannot remove the collection creator. The creator must always retain access."


def _path(kb_id: str) -> str:
    return f"/{kb_id}/permissions"


def _ids(kb_client: KBClient, kb_id: str) -> set[str]:
    resp = kb_client.get(_path(kb_id))
    assert resp.status_code == 200, resp.text[:500]
    return {p["id"] for p in resp.json()["permissions"]}


def _creator_mongo_id(kb_client: KBClient, kb_id: str) -> str:
    """The Mongo id of the only permission holder of a knowledge base nobody else was given."""
    resp = kb_client.get(_path(kb_id))
    assert resp.status_code == 200, resp.text[:500]
    (entry,) = resp.json()["permissions"]
    return str(entry["userId"])


def test_remove_a_user(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="WRITER")

    resp = kb_client.delete(_path(kb_id), json={"userIds": [second_user.user_id], "teamIds": []})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"kbId": kb_id, "userIds": [second_user.graph_id], "teamIds": []}
    assert second_user.graph_id not in _ids(kb_client, kb_id)
    assert request_as(second_user, "GET", f"/{kb_id}").status_code == 404

    again = kb_client.delete(_path(kb_id), json={"userIds": [second_user.user_id], "teamIds": []})
    assert again.status_code == 404, again.text[:500]
    assert again.json()["error"]["message"] == NOTHING_TO_REMOVE
    assert_strict_openapi_exchange(again, ROUTE)


def test_remove_a_team(kb_client: KBClient, pipeshub_client: PipeshubClient, make_kb: MakeKb) -> None:
    kb_id = make_kb()
    team_id = all_team_id(pipeshub_client.org_id)
    grant(pipeshub_client, kb_id, team_ids=[team_id])
    resp = kb_client.delete(_path(kb_id), json={"userIds": [], "teamIds": [team_id]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"kbId": kb_id, "userIds": [], "teamIds": [team_id]}
    assert team_id not in _ids(kb_client, kb_id)


def test_remove_by_a_member_owner(kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb) -> None:
    kb_id = make_kb(member_role="OWNER")
    resp = request_as(second_user, "DELETE", _path(kb_id), json={"userIds": [second_user.user_id], "teamIds": []})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert second_user.graph_id not in _ids(kb_client, kb_id)


def test_remove_ignores_other_fields_and_the_query(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="READER")
    with outside_request_contract("the validator drops body fields and query parameters it does not know"):
        resp = kb_client.delete(
            _path(kb_id), params={"notify": "true"}, json={"userIds": [second_user.user_id], "teamIds": [], "role": "OWNER"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("caller", ["the-creator", "another-owner"])
def test_remove_the_creator_is_forbidden(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb, caller: str
) -> None:
    kb_id = make_kb(member_role="OWNER")
    resp = kb_client.get(_path(kb_id))
    assert resp.status_code == 200, resp.text[:500]
    creator = next(p["userId"] for p in resp.json()["permissions"] if p["id"] != second_user.graph_id)
    body = {"userIds": [creator], "teamIds": []}

    if caller == "the-creator":
        resp = kb_client.delete(_path(kb_id), json=body)
    else:
        resp = request_as(second_user, "DELETE", _path(kb_id), json=body)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == CREATOR_STAYS
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"userIds": "someone", "teamIds": []}, id="user-ids-not-a-list"),
        pytest.param({"userIds": [], "teamIds": [7]}, id="team-id-not-a-string"),
    ],
)
def test_remove_rejects_a_body_outside_the_validator(kb_client: KBClient, payload: dict[str, Any]) -> None:
    resp = kb_client.delete(_path(MISSING_RECORD_ID), json=payload)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_with_two_empty_lists_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.delete(_path(MISSING_RECORD_ID), json={"userIds": [], "teamIds": []})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == "User IDs or team IDs are required"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"userIds": ["someone"]}, id="team-ids-missing"),
        pytest.param({"teamIds": ["some-team"]}, id="user-ids-missing"),
        pytest.param(None, id="no-body"),
    ],
)
def test_remove_without_both_id_lists_is_an_internal_error(kb_client: KBClient, payload: Any) -> None:
    resp = (
        kb_client.delete(_path(MISSING_RECORD_ID), json=payload)
        if payload is not None
        else kb_client.delete(_path(MISSING_RECORD_ID))
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_INTERNAL_SERVER_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_remove_with_a_graph_id_or_an_unknown_user_is_bad_request(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb(member_role="READER")
    for user_id in (second_user.graph_id, MISSING_RECORD_ID):
        resp = kb_client.delete(_path(kb_id), json={"userIds": [user_id], "teamIds": []})
        assert resp.status_code == 400, resp.text[:500]
        assert resp.json()["error"]["message"] == UNKNOWN_PEOPLE
        assert_strict_openapi_exchange(resp, ROUTE)
    assert second_user.graph_id in _ids(kb_client, kb_id)


def test_remove_a_user_without_a_permission_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    resp = kb_client.delete(_path(kb_id), json={"userIds": [second_user.user_id], "teamIds": []})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == NOTHING_TO_REMOVE
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_kb_id_that_is_not_a_uuid_is_rejected(kb_client: KBClient) -> None:
    resp = kb_client.delete(_path(MALFORMED_ID), json={"userIds": ["someone"], "teamIds": []})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["metadata"]["errors"][0]["field"] == "params.kbId"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_on_an_unknown_knowledge_base_is_not_found(kb_client: KBClient, second_user: SecondUser) -> None:
    resp = kb_client.delete(_path(MISSING_RECORD_ID), json={"userIds": [second_user.user_id], "teamIds": []})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_by_a_member_without_a_role_is_not_found(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb
) -> None:
    kb_id = make_kb()
    creator = _creator_mongo_id(kb_client, kb_id)
    resp = request_as(second_user, "DELETE", _path(kb_id), json={"userIds": [creator], "teamIds": []})
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Knowledge base not found"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("role", ["READER", "WRITER"])
def test_remove_by_a_non_owner_is_forbidden(
    kb_client: KBClient, second_user: SecondUser, make_kb: MakeKb, role: str
) -> None:
    kb_id = make_kb(member_role=role)
    resp = request_as(second_user, "DELETE", _path(kb_id), json={"userIds": [second_user.user_id], "teamIds": []})
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Only KB owners can remove permissions"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert second_user.graph_id in _ids(kb_client, kb_id)


def test_remove_kb_id_that_cannot_be_a_url_segment_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.delete(_path(UNSAFE_ID), json={"userIds": ["someone"], "teamIds": []})
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
def test_remove_rejects_unauthenticated_calls(kb_client: KBClient, headers: dict[str, str]) -> None:
    resp = kb_client.delete(_path(MISSING_RECORD_ID), auth=False, headers=headers, json={"userIds": ["x"], "teamIds": []})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_with_a_token_lacking_kb_delete_is_forbidden(
    kb_client: KBClient, unscoped_headers: dict[str, str]
) -> None:
    resp = kb_client.delete(
        _path(MISSING_RECORD_ID), auth=False, headers=unscoped_headers, json={"userIds": ["x"], "teamIds": []}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == "Insufficient scope. Required: kb:delete"
    assert_strict_openapi_exchange(resp, ROUTE)
