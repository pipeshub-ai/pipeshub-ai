"""Strict OpenAPI audit of GET /api/v1/notifications/stats."""

from __future__ import annotations

import datetime
from typing import Callable

import pytest
import requests
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/notifications/stats"
COUNT_FIELDS = ("unreadCount", "readCount", "archivedCount")


def _member_stats(user: SecondUser) -> dict[str, int]:
    resp = requests.get(
        f"{user.base_url}{ROUTE}", headers=user.headers, timeout=user.timeout
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == set(COUNT_FIELDS), body
    return body


def test_stats_counts_the_callers_notifications_per_status(
    second_user: SecondUser, seed_notification: Callable[..., str]
) -> None:
    before = _member_stats(second_user)

    for status in ("unread", "unread", "read", "archived"):
        seed_notification(status=status, assigned_to=second_user.user_id)

    after = _member_stats(second_user)
    assert after["unreadCount"] - before["unreadCount"] == 2, (before, after)
    assert after["readCount"] - before["readCount"] == 1, (before, after)
    assert after["archivedCount"] - before["archivedCount"] == 1, (before, after)


def test_stats_skips_deleted_expired_and_other_users_notifications(
    second_user: SecondUser, seed_notification: Callable[..., str]
) -> None:
    before = _member_stats(second_user)

    expired = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=31)
    seed_notification(assigned_to=second_user.user_id, isDeleted=True)
    seed_notification(assigned_to=second_user.user_id, createdAt=expired)
    seed_notification()

    assert _member_stats(second_user) == before


def test_stats_as_admin_ignores_unknown_query_params(
    pipeshub_client: PipeshubClient,
) -> None:
    with outside_request_contract("the validator is an empty object and the handler reads no query"):
        resp = pipeshub_client.request("GET", ROUTE, params={"status": "bogus"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    for field in COUNT_FIELDS:
        assert isinstance(body[field], int) and body[field] >= 0, body


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_stats_rejects_unauthenticated_calls(
    pipeshub_client: PipeshubClient, headers: dict[str, str]
) -> None:
    resp = pipeshub_client.request("GET", ROUTE, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
