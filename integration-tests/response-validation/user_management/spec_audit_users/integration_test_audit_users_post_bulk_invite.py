"""Strict OpenAPI audit of POST /api/v1/users/bulk/invite.

Mailpit is the SMTP relay of this stack, so invitation mail is really delivered
and the tests wait for it; every address is on a reserved domain and is removed
from Mailpit and from the org afterwards.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
from bson import ObjectId
from helper import mailpit
from helper.clients.users_client import UsersClient
from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from pymongo import MongoClient
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from users_audit_support import (
    INVALID_BEARER,
    MISSING_USER_ID,
    request_as,
    users_with_emails,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/bulk/invite"
PATH = "/bulk/invite"

QUEUED = {
    "message": "Invites queued. Emails are being sent in the background and may take a few minutes.",
    "queued": True,
}
ALL_ACTIVE = {"errorMessage": "All provided emails already have active accounts"}

NewAddress = Callable[[], str]


def _everyone_group_id(org_id: str) -> str:
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
        group = client[MONGO_DB_NAME].userGroups.find_one(
            {"orgId": ObjectId(org_id), "type": "everyone", "isDeleted": False}
        )
    assert group, "the org has no 'everyone' group"
    return str(group["_id"])


def _only_user(address: str) -> dict[str, Any]:
    (user,) = users_with_emails([address])
    return user


def test_admin_invites_a_new_address(users_client: UsersClient, invitee: NewAddress) -> None:
    address = invitee()
    seen = mailpit.message_ids(address)
    resp = users_client.post(PATH, json={"emails": [address], "role": "member"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == QUEUED
    user = _only_user(address)
    assert (user["role"], user["hasLoggedIn"], user["isDeleted"]) == ("member", False, False), user
    mailpit.wait_for_new_message(address, "You are invited to join", seen)


def test_group_ids_are_accepted(
    users_client: UsersClient, invitee: NewAddress, pipeshub_client: PipeshubClient
) -> None:
    address = invitee()
    resp = users_client.post(
        PATH, json={"emails": [address], "groupIds": [_everyone_group_id(pipeshub_client.org_id)]}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == QUEUED


def test_a_pending_address_is_invited_again(users_client: UsersClient, invitee: NewAddress) -> None:
    address = invitee()
    first = users_client.post(PATH, json={"emails": [address]})
    assert first.status_code == 200, first.text[:500]
    seen = mailpit.message_ids(address)
    resp = users_client.post(PATH, json={"emails": [address]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == QUEUED
    assert len(users_with_emails([address])) == 1
    mailpit.wait_for_new_message(address, "You are invited to join", seen)


def test_a_deleted_address_is_restored(users_client: UsersClient, invitee: NewAddress) -> None:
    address = invitee()
    assert users_client.post(PATH, json={"emails": [address]}).status_code == 200
    removed = users_client.delete_user(str(_only_user(address)["_id"]))
    assert removed.status_code == 200, removed.text[:500]
    seen = mailpit.message_ids(address)

    resp = users_client.post(PATH, json={"emails": [address]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == QUEUED
    assert _only_user(address)["isDeleted"] is False
    mailpit.wait_for_new_message(address, "You are invited to re-join", seen)


@pytest.mark.parametrize("case", ["as-stored", "upper-case"])
def test_only_active_accounts_is_a_200_message(
    users_client: UsersClient, second_user: SecondUser, case: str
) -> None:
    address = second_user.email if case == "as-stored" else second_user.email.upper()
    resp = users_client.post(PATH, json={"emails": [address]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == ALL_ACTIVE


def test_surrounding_whitespace_is_trimmed(users_client: UsersClient, second_user: SecondUser) -> None:
    with outside_request_contract("a padded address, which the global trimming middleware repairs"):
        resp = users_client.post(PATH, json={"emails": [f"  {second_user.email} "]})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == ALL_ACTIVE


def test_unlisted_body_fields_are_dropped(users_client: UsersClient, second_user: SecondUser) -> None:
    with outside_request_contract("an unlisted body field, which the validator strips"):
        resp = users_client.post(PATH, json={"emails": [second_user.email], "sendEmail": False})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == ALL_ACTIVE


def test_a_member_may_invite_as_member(second_user: SecondUser, invitee: NewAddress) -> None:
    address = invitee()
    resp = request_as(second_user, "POST", PATH, json={"emails": [address]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == QUEUED


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"role": "admin"}, "Members can only invite users as member"),
        ({"groupIds": [MISSING_USER_ID]}, "Members cannot assign groups when inviting"),
    ],
    ids=["admin-role", "group-ids"],
)
def test_a_member_asking_for_more_is_forbidden(
    second_user: SecondUser, invitee: NewAddress, body: dict[str, Any], message: str
) -> None:
    address = invitee()
    resp = request_as(second_user, "POST", PATH, json={"emails": [address], **body})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
    assert users_with_emails([address]) == []


@pytest.mark.parametrize(
    "emails",
    [["not-an-email"], ["spec@audit"], ["spec audit@example.com"], ["ok@example.com", "a@b..com"]],
    ids=["no-at", "no-dot-in-domain", "space", "one-bad-of-two"],
)
def test_an_address_the_handler_rejects_is_a_bad_request(
    users_client: UsersClient, emails: list[str]
) -> None:
    resp = users_client.post(PATH, json={"emails": emails})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"] == {
        "code": "HTTP_BAD_REQUEST",
        "message": "Invalid emails are found",
        "requestId": resp.json()["error"]["requestId"],
    }
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert users_with_emails(["ok@example.com"]) == []


def test_a_malformed_group_id_is_an_internal_error(
    users_client: UsersClient, second_user: SecondUser
) -> None:
    # Not validated as an ObjectId, so the group update fails inside the handler.
    resp = users_client.post(PATH, json={"emails": [second_user.email], "groupIds": ["not-a-group"]})
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"emails": []},
        {"emails": "spec-audit@example.com"},
        {"emails": [5]},
        {"emails": ["spec-audit@example.com"], "role": "owner"},
        {"emails": ["spec-audit@example.com"], "groupIds": MISSING_USER_ID},
        {"emails": ["spec-audit@example.com"], "groupIds": [5]},
    ],
    ids=["missing", "empty", "string", "number-item", "role-unknown", "groupIds-string", "groupIds-number"],
)
def test_an_invalid_body_is_a_validation_error(
    users_client: UsersClient, body: dict[str, Any]
) -> None:
    resp = users_client.post(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.post(
        PATH, auth=False, headers=headers, json={"emails": ["spec-audit@example.com"]}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
