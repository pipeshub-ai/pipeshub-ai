"""
Unit tests for updated SlackDataSource._handle_slack_error() in
app/sources/external/slack/slack.py.

The key change in the diff: error field now stores the raw Slack API error
code (e.g. "not_in_channel") so callers can do reliable equality checks,
and message carries the human-readable explanation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_datasource():
    """Create a SlackDataSource with a mocked underlying client."""
    from app.sources.external.slack.slack import SlackDataSource
    mock_client = MagicMock()
    mock_client.get_client = MagicMock(return_value=MagicMock())
    ds = SlackDataSource.__new__(SlackDataSource)
    ds.client = mock_client
    return ds


class TestHandleSlackError:
    """Tests for the normalised error field in _handle_slack_error."""

    @pytest.mark.asyncio
    async def test_not_allowed_token_type(self):
        ds = _make_datasource()
        exc = Exception("not_allowed_token_type error occurred")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "not_allowed_token_type"
        assert resp.message is not None
        assert len(resp.message) > 0

    @pytest.mark.asyncio
    async def test_invalid_auth(self):
        ds = _make_datasource()
        exc = Exception("invalid_auth: invalid token")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "invalid_auth"
        assert "Invalid Slack token" in resp.message

    @pytest.mark.asyncio
    async def test_missing_scope(self):
        ds = _make_datasource()
        exc = Exception("missing_scope: channels:read is required")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "missing_scope"
        assert resp.message is not None

    @pytest.mark.asyncio
    async def test_account_inactive(self):
        ds = _make_datasource()
        exc = Exception("account_inactive")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "account_inactive"

    @pytest.mark.asyncio
    async def test_token_revoked(self):
        ds = _make_datasource()
        exc = Exception("token_revoked")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "token_revoked"

    @pytest.mark.asyncio
    async def test_channel_not_found(self):
        ds = _make_datasource()
        exc = Exception("channel_not_found")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "channel_not_found"

    @pytest.mark.asyncio
    async def test_not_in_channel(self):
        ds = _make_datasource()
        exc = Exception("not_in_channel")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "not_in_channel"
        assert resp.message is not None

    @pytest.mark.asyncio
    async def test_unknown_error_preserves_full_message(self):
        ds = _make_datasource()
        exc = Exception("some_completely_unknown_error_code")
        resp = await ds._handle_slack_error(exc)
        assert resp.success is False
        assert resp.error == "unknown_error"
        assert "some_completely_unknown_error_code" in (resp.message or "")

    @pytest.mark.asyncio
    async def test_error_field_is_code_not_human_text(self):
        """Ensure the error field is the raw error code, not a long sentence."""
        ds = _make_datasource()
        exc = Exception("not_in_channel")
        resp = await ds._handle_slack_error(exc)
        # The error field should be exactly the code, not the long human message
        assert resp.error == "not_in_channel"
        # The human message goes in the message field
        assert "Bot" in (resp.message or "") or "member" in (resp.message or "").lower()


class TestSlackDataSourceHandleApiCalls:
    """Smoke tests for API wrappers (call web client + _handle_slack_response)."""

    @pytest.mark.asyncio
    async def test_conversations_history_success(self):
        from app.sources.external.slack.slack import SlackDataSource

        mock_wc = MagicMock()
        mock_wc.conversations_history = MagicMock(
            return_value={"ok": True, "messages": []}
        )

        ds = SlackDataSource.__new__(SlackDataSource)
        ds.client = mock_wc

        resp = await ds.conversations_history(channel="C123")
        assert resp.success is True
        assert resp.data == {"ok": True, "messages": []}
        mock_wc.conversations_history.assert_called_once()
