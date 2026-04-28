"""Unit tests for staged changes in `app.agents.actions.slack.slack`.

Covers the slack-toolset diff:
* `GetDmHistoryInput` schema
* `SearchMessagesInput` schema (new `with_user` / `from_user` fields)
* `_resolve_user_handle` helper
* `get_dm_history` tool method
* `search_messages` tool method (modifier-builder behaviour)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.slack.config import SlackResponse
from app.agents.actions.slack.slack import (
    AmbiguousUserError,
    GetDmHistoryInput,
    SearchMessagesInput,
    Slack,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_slack() -> Slack:
    """Instantiate Slack bypassing real client construction; stub
    `self.client` (the `SlackDataSource`) with a MagicMock so each test can
    set per-method AsyncMock return values."""
    slack = Slack.__new__(Slack)
    slack.client = MagicMock()
    return slack


def _ok(data):
    """Mimic the dict shape Slack's WebClient returns on success."""
    return {"ok": True, **data}


def _fail(error: str):
    return {"ok": False, "error": error}


# ===========================================================================
# Pydantic input schemas
# ===========================================================================

class TestGetDmHistoryInput:
    def test_user_required(self):
        with pytest.raises(Exception):
            GetDmHistoryInput()  # type: ignore[call-arg]

    def test_defaults_limit_none(self):
        data = GetDmHistoryInput(user="alice@example.com")
        assert data.user == "alice@example.com"
        assert data.limit is None

    def test_limit_set(self):
        data = GetDmHistoryInput(user="U123ABC456", limit=50)
        assert data.limit == 50


class TestSearchMessagesInput:
    def test_query_only(self):
        data = SearchMessagesInput(query="roadmap")
        assert data.query == "roadmap"
        assert data.channel is None
        assert data.with_user is None
        assert data.from_user is None
        assert data.count is None
        assert data.sort is None

    def test_with_user_set(self):
        data = SearchMessagesInput(query="q", with_user="alice@example.com")
        assert data.with_user == "alice@example.com"

    def test_from_user_set(self):
        data = SearchMessagesInput(query="q", from_user="U123ABC456")
        assert data.from_user == "U123ABC456"

    def test_all_filters_set(self):
        data = SearchMessagesInput(
            query="release",
            channel="#general",
            with_user="alice",
            from_user="bob",
            count=20,
            sort="timestamp",
        )
        assert data.channel == "#general"
        assert data.with_user == "alice"
        assert data.from_user == "bob"
        assert data.count == 20
        assert data.sort == "timestamp"


# ===========================================================================
# _resolve_user_handle
# ===========================================================================

class TestResolveUserHandle:
    @pytest.mark.asyncio
    async def test_returns_handle_on_success(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(
            return_value=_ok({"user": {"id": "U123ABC456", "name": "alice"}})
        )
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ):
            handle = await slack._resolve_user_handle("alice@example.com")
        assert handle == "alice"
        slack.client.users_info.assert_awaited_once_with(user="U123ABC456")

    @pytest.mark.asyncio
    async def test_returns_none_when_user_not_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value=None)
        ):
            handle = await slack._resolve_user_handle("ghost@example.com")
        assert handle is None
        # users_info must not be called when the identifier doesn't resolve
        slack.client.users_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_none_when_users_info_fails(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_fail("user_not_found"))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ):
            handle = await slack._resolve_user_handle("alice")
        assert handle is None

    @pytest.mark.asyncio
    async def test_returns_none_when_handle_missing(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(
            return_value=_ok({"user": {"id": "U123ABC456"}})  # no "name"
        )
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ):
            handle = await slack._resolve_user_handle("alice")
        assert handle is None

    @pytest.mark.asyncio
    async def test_swallow_users_info_exception(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ):
            handle = await slack._resolve_user_handle("alice")
        assert handle is None

    @pytest.mark.asyncio
    async def test_propagates_ambiguous_user_error(self):
        slack = _build_slack()
        matches = [{"id": "U1"}, {"id": "U2"}]
        with patch.object(
            slack,
            "_resolve_user_identifier",
            AsyncMock(side_effect=AmbiguousUserError("alice", matches)),
        ):
            with pytest.raises(AmbiguousUserError):
                await slack._resolve_user_handle("alice")


# ===========================================================================
# get_dm_history
# ===========================================================================

class TestGetDmHistory:
    @pytest.mark.asyncio
    async def test_success_delegates_to_get_channel_history(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock(
            return_value=_ok({"channel": {"id": "D9999"}})
        )
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ), patch.object(
            slack, "get_channel_history", AsyncMock(return_value=(True, '{"ok":true}'))
        ) as gch:
            ok, payload = await slack.get_dm_history("alice@example.com", limit=25)

        assert ok is True
        assert payload == '{"ok":true}'
        slack.client.conversations_open.assert_awaited_once_with(users=["U123ABC456"])
        gch.assert_awaited_once_with("D9999", 25)

    @pytest.mark.asyncio
    async def test_user_not_found_returns_error(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock()
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value=None)
        ):
            ok, payload = await slack.get_dm_history("ghost@example.com")

        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert "ghost@example.com" in body["error"]
        assert "not found" in body["error"].lower()
        slack.client.conversations_open.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ambiguous_user_returns_disambiguation_message(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock()
        matches = [
            {"id": "U1", "name": "alice", "real_name": "Alice One",
             "display_name": "alice", "email": "alice1@example.com"},
            {"id": "U2", "name": "alice", "real_name": "Alice Two",
             "display_name": "alice", "email": "alice2@example.com"},
        ]
        with patch.object(
            slack,
            "_resolve_user_identifier",
            AsyncMock(side_effect=AmbiguousUserError("alice", matches)),
        ):
            ok, payload = await slack.get_dm_history("alice")

        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert "Multiple users found matching 'alice'" in body["error"]
        assert "Alice One" in body["error"]
        assert "Alice Two" in body["error"]
        assert "alice1@example.com" in body["error"]
        assert "U1" in body["error"] and "U2" in body["error"]
        slack.client.conversations_open.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_conversations_open_failure_propagates_error(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock(
            return_value=_fail("user_disabled")
        )
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ), patch.object(
            slack, "get_channel_history", AsyncMock()
        ) as gch:
            ok, payload = await slack.get_dm_history("alice@example.com")

        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert body["error"] == "user_disabled"
        gch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_channel_id_returns_error(self):
        slack = _build_slack()
        # Successful open but no channel.id → must surface "Failed to open DM channel"
        slack.client.conversations_open = AsyncMock(return_value=_ok({"channel": {}}))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ), patch.object(
            slack, "get_channel_history", AsyncMock()
        ) as gch:
            ok, payload = await slack.get_dm_history("alice@example.com")

        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert body["error"] == "Failed to open DM channel"
        gch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unexpected_exception_wrapped_in_error_response(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock(side_effect=RuntimeError("net"))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ):
            ok, payload = await slack.get_dm_history("alice@example.com")

        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert "net" in body["error"]


# ===========================================================================
# search_messages
# ===========================================================================

class TestSearchMessages:
    @pytest.mark.asyncio
    async def test_query_only_no_modifiers(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        ok, payload = await slack.search_messages(query="roadmap")
        assert ok is True
        slack.client.search_messages.assert_awaited_once_with(
            query="roadmap", count=None, sort=None
        )

    @pytest.mark.asyncio
    async def test_channel_modifier_strips_hash(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        await slack.search_messages(query="hello", channel="#general")
        args = slack.client.search_messages.await_args.kwargs
        assert args["query"] == "in:general hello"

    @pytest.mark.asyncio
    async def test_channel_modifier_without_hash(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        await slack.search_messages(query="hello", channel="general")
        args = slack.client.search_messages.await_args.kwargs
        assert args["query"] == "in:general hello"

    @pytest.mark.asyncio
    async def test_with_user_modifier_uses_resolved_handle(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))
        with patch.object(
            slack, "_resolve_user_handle", AsyncMock(return_value="alice")
        ):
            await slack.search_messages(query="q", with_user="alice@example.com")

        args = slack.client.search_messages.await_args.kwargs
        assert args["query"] == "with:@alice q"

    @pytest.mark.asyncio
    async def test_from_user_modifier_uses_resolved_handle(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))
        with patch.object(
            slack, "_resolve_user_handle", AsyncMock(return_value="bob")
        ):
            await slack.search_messages(query="deploy", from_user="bob@example.com")

        args = slack.client.search_messages.await_args.kwargs
        assert args["query"] == "from:@bob deploy"

    @pytest.mark.asyncio
    async def test_combined_modifiers_in_order(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        async def fake_handle(identifier):
            return {"alice@example.com": "alice", "bob@example.com": "bob"}[identifier]

        with patch.object(slack, "_resolve_user_handle", AsyncMock(side_effect=fake_handle)):
            await slack.search_messages(
                query="release",
                channel="#general",
                with_user="alice@example.com",
                from_user="bob@example.com",
                count=10,
                sort="timestamp",
            )

        args = slack.client.search_messages.await_args.kwargs
        # Modifiers order: channel, with, from, then the raw query
        assert args["query"] == "in:general with:@alice from:@bob release"
        assert args["count"] == 10
        assert args["sort"] == "timestamp"

    @pytest.mark.asyncio
    async def test_unresolvable_with_user_returns_error_no_search(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock()
        with patch.object(slack, "_resolve_user_handle", AsyncMock(return_value=None)):
            ok, payload = await slack.search_messages(query="q", with_user="ghost")

        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert "Could not resolve 'ghost' for 'with_user'" in body["error"]
        slack.client.search_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unresolvable_from_user_returns_error_no_search(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock()
        with patch.object(slack, "_resolve_user_handle", AsyncMock(return_value=None)):
            ok, payload = await slack.search_messages(query="q", from_user="ghost")

        assert ok is False
        body = json.loads(payload)
        assert "Could not resolve 'ghost' for 'from_user'" in body["error"]
        slack.client.search_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ambiguous_with_user_returns_disambiguation_message(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock()
        matches = [
            {"id": "U1", "name": "alice", "real_name": "Alice One",
             "email": "alice1@example.com"},
            {"id": "U2", "name": "alice", "real_name": "Alice Two",
             "email": "alice2@example.com"},
        ]
        with patch.object(
            slack,
            "_resolve_user_handle",
            AsyncMock(side_effect=AmbiguousUserError("alice", matches)),
        ):
            ok, payload = await slack.search_messages(query="q", with_user="alice")

        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert "Multiple users found matching 'alice' for 'with_user'" in body["error"]
        assert "Alice One" in body["error"] and "Alice Two" in body["error"]
        slack.client.search_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ambiguous_from_user_uses_from_user_label(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock()
        matches = [{"id": "U1", "name": "bob"}, {"id": "U2", "name": "bob"}]
        with patch.object(
            slack,
            "_resolve_user_handle",
            AsyncMock(side_effect=AmbiguousUserError("bob", matches)),
        ):
            ok, payload = await slack.search_messages(query="q", from_user="bob")

        assert ok is False
        body = json.loads(payload)
        assert "for 'from_user'" in body["error"]

    @pytest.mark.asyncio
    async def test_unexpected_exception_wrapped_in_error_response(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(side_effect=RuntimeError("net"))

        ok, payload = await slack.search_messages(query="q")
        assert ok is False
        body = json.loads(payload)
        assert body["success"] is False
        assert "net" in body["error"]

    @pytest.mark.asyncio
    async def test_failed_search_response_returned_as_error(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_fail("rate_limited"))

        ok, payload = await slack.search_messages(query="q")
        assert ok is False
        body = json.loads(payload)
        assert body["error"] == "rate_limited"
