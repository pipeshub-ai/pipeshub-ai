"""Strict OpenAPI audit of PATCH /api/v1/notifications/:id/unread."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from notifications_audit_support import MALFORMED_NOTIFICATION_ID, MISSING_NOTIFICATION_ID
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications/:id/unread"


def test_mark_unread_returns_updated_notification(
    notifications_client: Any, seed_notification: Callable[..., str]
) -> None:
    notification_id = seed_notification(status="read")

    resp = notifications_client.set_state(notification_id, "unread")

    assert resp.status_code == 200, resp.text[:500]
    notification = resp.json()["notification"]
    assert notification["_id"] == notification_id
    assert notification["status"] == "unread"
    assert_strict_openapi_response(resp, ROUTE)


def test_mark_unread_without_token_is_unauthorized(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MISSING_NOTIFICATION_ID, "unread", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_mark_unread_malformed_id_fails_validation(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MALFORMED_NOTIFICATION_ID, "unread")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_mark_unread_unknown_id_is_not_found(notifications_client: Any) -> None:
    resp = notifications_client.set_state(MISSING_NOTIFICATION_ID, "unread")

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json() == {"message": "Notification not found"}
    assert_strict_openapi_response(resp, ROUTE)


def test_mark_unread_archived_notification_is_not_found(
    notifications_client: Any, seed_notification: Callable[..., str]
) -> None:
    # The update filter excludes archived documents, so an existing id still 404s.
    notification_id = seed_notification(status="archived")

    resp = notifications_client.set_state(notification_id, "unread")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
