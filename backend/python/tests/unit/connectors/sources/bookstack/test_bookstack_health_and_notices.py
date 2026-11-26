"""A run's health (``sync_health.py``), its notifications (``notices.py``), and BookStack answers with their status
(``BookStackDataSource._json_response``)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.bookstack.notices import BookStackNotices
from app.connectors.sources.bookstack.sync_health import (
    BookStackSyncHealth,
    BookStackSyncIncompleteError,
)
from app.services.notification.types import NotificationType
from app.sources.external.bookstack.bookstack import BookStackDataSource


def test_a_healthy_run_raises_nothing() -> None:
    BookStackSyncHealth().raise_if_failed()


def test_a_failure_fails_the_run() -> None:
    health = BookStackSyncHealth()
    health.note_status("the page listing", 503, "content")
    with pytest.raises(BookStackSyncIncompleteError, match="page listing"):
        health.raise_if_failed()
    assert not health.token_refused and not health.missing_permissions


def test_401_and_the_token_guards_403s_are_the_token() -> None:
    for status, message in ((401, "No matching API token was found for the provided authorization token"),
                            (403, "The authorization token used has expired"),
                            (403, "The owner of the used API token does not have permission to make API calls")):
        health = BookStackSyncHealth()
        health.note_status("x", status, "users-manage", message)
        assert health.token_refused and not health.missing_permissions


def test_another_403_is_a_missing_permission() -> None:
    health = BookStackSyncHealth()
    health.note_status("the audit log", 403, "settings-manage", "You do not have permission to perform the requested action.")
    assert health.missing_permissions == {"settings-manage"} and not health.token_refused


@pytest.mark.asyncio
async def test_a_refused_token_sends_one_plain_notice() -> None:
    notify = AsyncMock()
    health = BookStackSyncHealth(token_refused=True, missing_permissions={"users-manage"})
    await BookStackNotices(notify, lambda event: f"Team wiki: {event}").report(health)
    notify.assert_awaited_once()
    sent = notify.await_args.kwargs
    assert sent["type"] == NotificationType.CONNECTOR_AUTH_ERROR
    assert sent["title"].startswith("Team wiki") and "token" in sent["title"].lower()
    assert "HTTP" not in sent["message"] and "401" not in sent["message"]


@pytest.mark.asyncio
async def test_missing_permissions_are_named_as_bookstack_names_them() -> None:
    notify = AsyncMock()
    await BookStackNotices(notify, str).report(BookStackSyncHealth(missing_permissions={"settings-manage"}))
    assert "Manage app settings" in notify.await_args.kwargs["message"]


@pytest.mark.asyncio
async def test_an_outage_notifies_nobody() -> None:
    notify = AsyncMock()
    health = BookStackSyncHealth()
    health.note_status("the page listing", 503, "content")
    await BookStackNotices(notify, str).report(health)
    notify.assert_not_awaited()


def _http(status: int, body: bytes, parsed: object) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.bytes.return_value = body
    response.json.return_value = parsed
    return response


def test_an_error_body_is_a_failure_with_its_status_and_message() -> None:
    answer = BookStackDataSource._json_response(_http(429, b"{...}", {"error": {"message": "An error occurred",
                                                                                 "code": 429}}))
    assert not answer.success and answer.status_code == 429 and answer.error == "An error occurred"


def test_an_empty_204_is_a_success() -> None:
    answer = BookStackDataSource._json_response(_http(204, b"", None))
    assert answer.success and answer.data == {} and answer.status_code == 204


def test_a_listing_is_a_success_with_its_status() -> None:
    answer = BookStackDataSource._json_response(_http(200, b"{...}", {"data": [], "total": 0}))
    assert answer.success and answer.data == {"data": [], "total": 0} and answer.status_code == 200


def test_an_html_error_page_is_a_failure_with_its_status() -> None:
    response = _http(502, b"<html>Bad Gateway</html>", None)
    response.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
    answer = BookStackDataSource._json_response(response)
    assert not answer.success and answer.status_code == 502 and answer.error == "HTTP 502" and answer.data is None


def test_a_success_that_is_not_json_is_not_read_as_data() -> None:
    response = _http(200, b"<html>Login</html>", None)
    response.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
    with pytest.raises(ValueError):
        BookStackDataSource._json_response(response)
