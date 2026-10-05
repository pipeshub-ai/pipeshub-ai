"""Strict OpenAPI audit of PATCH /api/v1/notifications/:id/read."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from bson import ObjectId
from pymongo.collection import Collection
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications/:id/read"

# Kept local: several conftest modules are importable under the bare name "conftest".
MISSING_NOTIFICATION_ID = "0123456789abcdef01234567"
MALFORMED_NOTIFICATION_ID = "not-an-object-id"


def test_mark_read_returns_updated_notification(
    notifications_client: Any,
    seed_notification: Callable[..., str],
    admin_user_id: str,
) -> None:
    notification_id = seed_notification(status="unread")

    resp = notifications_client.set_state(notification_id, "read")

    assert resp.status_code == 200, resp.text[:500]
    notification = resp.json()["notification"]
    assert notification["_id"] == notification_id
    assert notification["status"] == "read"
    assert notification["assignedTo"] == admin_user_id
    assert_strict_openapi_response(resp, ROUTE)


def test_mark_read_without_token_is_unauthorized(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MISSING_NOTIFICATION_ID, "read", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("notification_id", "expected_status"),
    [
        pytest.param(MALFORMED_NOTIFICATION_ID, 400, id="malformed-id"),
        pytest.param(MISSING_NOTIFICATION_ID, 404, id="missing-id"),
    ],
)
def test_mark_read_rejects_unusable_id(
    notifications_client: Any, notification_id: str, expected_status: int
) -> None:
    resp = notifications_client.set_state(notification_id, "read")

    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_mark_read_on_archived_notification_is_not_found(
    notifications_client: Any,
    seed_notification: Callable[..., str],
    notifications_collection: Collection,
) -> None:
    # The handler's filter excludes archived documents, so an existing id still 404s.
    notification_id = seed_notification(status="archived")

    resp = notifications_client.set_state(notification_id, "read")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"message": "Notification not found"}
    stored = notifications_collection.find_one({"_id": ObjectId(notification_id)})
    assert stored is not None and stored["status"] == "archived"
    assert_strict_openapi_response(resp, ROUTE)
