"""Slack streaming failures: the raw Slack code has to survive the SDK layer.

`SlackResponse.validate()` raises `SlackApiError` for every non-ok response, so
`_handle_slack_error` — not the `ok: false` branch of `_handle_slack_response` —
is what every Slack failure actually goes through. It used to collapse anything
outside a seven-code allow-list to "unknown_error", which made most of the
mappings in `slack/common/stream_errors.py` unreachable and put a deleted file
back on the blanket 500 that module exists to remove.

These tests drive a real `SlackApiError` end to end rather than constructing a
`SlackResponse` by hand, which is what let the flattening go unnoticed.
"""

from __future__ import annotations

import pytest

from app.connectors.sources.slack.common.stream_errors import slack_stream_error
from app.sources.external.slack.slack import SlackDataSource

slack_errors = pytest.importorskip("slack_sdk.errors")
slack_web = pytest.importorskip("slack_sdk.web")


def _api_error(code, *, status_code=200, headers=None):
    """A `SlackApiError` shaped exactly like the SDK builds one."""
    body = {"ok": False}
    if code:
        body["error"] = code
    response = slack_web.SlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.com/api/files.info",
        req_args={},
        data=body,
        headers=headers or {},
        status_code=status_code,
    )
    return slack_errors.SlackApiError("The request to the Slack API failed.", response)


def _datasource():
    ds = SlackDataSource.__new__(SlackDataSource)
    ds.client = None
    return ds


async def _mapped(exc):
    return slack_stream_error(await _datasource()._handle_slack_error(exc), connector="Slack")


class TestRawCodeReachesTheMapping:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("file_not_found", 404),
            ("file_deleted", 404),
            ("message_not_found", 404),
            ("thread_not_found", 404),
            ("not_authed", 409),
            ("invalid_auth", 409),
            ("token_revoked", 409),
            ("missing_scope", 403),
            ("not_in_channel", 403),
            ("channel_not_found", 403),
        ],
    )
    @pytest.mark.asyncio
    async def test_code_maps_to_its_own_status(self, code, expected) -> None:
        assert (await _mapped(_api_error(code))).status_code == expected

    @pytest.mark.asyncio
    async def test_unrecognised_code_on_a_200_is_still_a_500(self) -> None:
        """A code we don't know is no evidence the item is gone."""
        err = await _mapped(_api_error("some_new_slack_code"))
        assert err.status_code == 500


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_retry_after_reaches_the_client(self) -> None:
        err = await _mapped(
            _api_error("ratelimited", status_code=429, headers={"retry-after": "42"})
        )
        assert err.status_code == 429
        assert err.headers == {"Retry-After": "42"}

    @pytest.mark.asyncio
    async def test_transport_429_without_a_known_code(self) -> None:
        """A proxy in front of Slack can 429 with no Slack code at all."""
        err = await _mapped(_api_error(None, status_code=429, headers={"Retry-After": "7"}))
        assert err.status_code == 429
        assert err.headers == {"Retry-After": "7"}

    @pytest.mark.asyncio
    async def test_non_numeric_retry_after_is_dropped(self) -> None:
        """A CRLF in the header would make h11 drop the connection at write time."""
        err = await _mapped(
            _api_error(
                "ratelimited", status_code=429, headers={"retry-after": "12\r\nX-Injected: 1"}
            )
        )
        assert err.status_code == 429
        assert not err.headers


class TestHandlerContractUnchanged:
    """`_handle_slack_error` is on the sync paths too: it must not start raising."""

    @pytest.mark.asyncio
    async def test_known_code_keeps_its_human_message(self) -> None:
        resp = await _datasource()._handle_slack_error(_api_error("not_in_channel"))
        assert resp.success is False
        assert resp.error == "not_in_channel"
        assert "invite the bot" in (resp.message or "").lower()

    @pytest.mark.asyncio
    async def test_raw_code_is_preserved_verbatim(self) -> None:
        resp = await _datasource()._handle_slack_error(_api_error("file_deleted"))
        assert resp.error == "file_deleted"
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_error_without_a_response_stays_unknown(self) -> None:
        resp = await _datasource()._handle_slack_error(RuntimeError("kaboom"))
        assert resp.success is False
        assert resp.error == "unknown_error"
        assert "kaboom" in (resp.message or "")
        assert resp.status_code is None
        assert resp.retry_after is None


class TestMissingResponse:
    def test_none_response_is_a_500(self) -> None:
        assert slack_stream_error(None, connector="Slack").status_code == 500
