"""Strict OpenAPI audit of DELETE /api/v1/notifications/:id."""

from __future__ import annotations

from bson import ObjectId
from pymongo.collection import Collection

import pytest
from notifications_audit_support import (
    MALFORMED_NOTIFICATION_ID,
    MISSING_NOTIFICATION_ID,
    NotificationsClient,
    SeedNotification,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications/:id"


def test_delete_soft_deletes_then_reports_not_found(
    notifications_client: NotificationsClient,
    seed_notification: SeedNotification,
    notifications_collection: Collection,
    admin_user_id: str,
) -> None:
    notification_id = seed_notification()

    resp = notifications_client.remove(notification_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True}

    stored = notifications_collection.find_one({"_id": ObjectId(notification_id)})
    assert stored is not None, "DELETE must soft-delete, not remove the document"
    assert stored["isDeleted"] is True
    assert str(stored["deletedBy"]) == admin_user_id

    again = notifications_client.remove(notification_id)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)
    assert again.json() == {"message": "Notification not found"}


def test_member_deletes_own_archived_notification(
    second_user: SecondUser,
    seed_notification: SeedNotification,
    notifications_collection: Collection,
) -> None:
    # Unlike read/unread/archive, the delete filter keeps archived documents in scope.
    notification_id = seed_notification(
        status="archived", assigned_to=second_user.user_id
    )

    resp = request_as(second_user, "DELETE", f"/{notification_id}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True}

    stored = notifications_collection.find_one({"_id": ObjectId(notification_id)})
    assert stored is not None
    assert stored["isDeleted"] is True
    assert stored["status"] == "archived"


def test_delete_another_users_notification_is_not_found(
    notifications_client: NotificationsClient,
    second_user: SecondUser,
    seed_notification: SeedNotification,
    notifications_collection: Collection,
) -> None:
    notification_id = seed_notification(assigned_to=second_user.user_id)

    resp = notifications_client.remove(notification_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Notification not found"}

    stored = notifications_collection.find_one({"_id": ObjectId(notification_id)})
    assert stored is not None
    assert stored["isDeleted"] is False


def test_delete_malformed_id_is_rejected(
    notifications_client: NotificationsClient,
) -> None:
    resp = notifications_client.remove(MALFORMED_NOTIFICATION_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_without_token_is_unauthorized(
    notifications_client: NotificationsClient,
) -> None:
    resp = notifications_client.remove(MISSING_NOTIFICATION_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
