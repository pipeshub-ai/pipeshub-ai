"""Strict OpenAPI audit of PATCH /api/v1/notifications/read-all."""

from __future__ import annotations

import pytest
import requests
from bson import ObjectId
from notifications_audit_support import NOTIFICATIONS_BASE, SeedNotification, request_as
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_response

from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications/read-all"


def _status_of(collection: Collection, notification_id: str) -> str:
    document = collection.find_one({"_id": ObjectId(notification_id)})
    assert document is not None, f"seeded notification {notification_id} disappeared"
    return document["status"]


# Always called as the member: read-all flips every unread notification of the
# caller, and the admin's inbox is shared by every suite on the stack.
def test_read_all_flips_only_the_callers_unread(
    second_user: SecondUser,
    seed_notification: SeedNotification,
    notifications_collection: Collection,
) -> None:
    own_unread = [seed_notification(assigned_to=second_user.user_id) for _ in range(2)]
    own_archived = seed_notification(status="archived", assigned_to=second_user.user_id)
    admins_unread = seed_notification()

    resp = request_as(second_user, "PATCH", "/read-all")

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True, body
    assert body["modifiedCount"] >= len(own_unread), body
    for notification_id in own_unread:
        assert _status_of(notifications_collection, notification_id) == "read"
    assert _status_of(notifications_collection, own_archived) == "archived"
    assert _status_of(notifications_collection, admins_unread) == "unread"
    assert_strict_openapi_response(resp, ROUTE)


def test_read_all_ignores_unexpected_query_and_body(second_user: SecondUser) -> None:
    """The route's zod schema is an empty passthrough object, so nothing is rejected."""
    resp = request_as(
        second_user,
        "PATCH",
        "/read-all",
        params={"status": "archived", "limit": "0"},
        json={"ids": ["not-an-object-id"], "status": "unread"},
    )

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True, body
    assert isinstance(body["modifiedCount"], int), body
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Basic dXNlcjpwYXNz"}, id="non-bearer-scheme"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-jwt"),
    ],
)
def test_read_all_rejects_missing_or_invalid_token(
    pipeshub_client: PipeshubClient, headers: dict[str, str]
) -> None:
    resp = requests.patch(
        f"{pipeshub_client.base_url}{NOTIFICATIONS_BASE}/read-all",
        headers=headers,
        timeout=pipeshub_client.timeout_seconds,
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
