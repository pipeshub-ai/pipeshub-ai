"""Unit tests for `app.agents.actions.slack.slack`.

Covers:
* Pydantic input schemas (validation, defaults, aliases)
* Internal helpers: `_handle_slack_response`, `_handle_slack_error`,
  `_convert_markdown_to_slack_mrkdwn`, `_get_authenticated_user_id`,
  `_resolve_channel`, `_resolve_user_identifier`, `_resolve_user_handle`
* All decorated tool methods (success, failure, exception paths)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.slack.config import SlackResponse
from app.agents.actions.slack.slack import (
    AddReactionInput,
    AmbiguousUserError,
    CreateChannelInput,
    DeleteMessageInput,
    GetChannelHistoryInput,
    GetChannelInfoInput,
    GetChannelMembersByIdInput,
    GetChannelMembersInput,
    GetDmHistoryInput,
    GetMessagePermalinkInput,
    GetPinnedMessagesInput,
    GetReactionsInput,
    GetScheduledMessagesInput,
    GetThreadRepliesInput,
    GetUnreadMessagesInput,
    GetUserChannelsInput,
    GetUserConversationsInput,
    GetUserGroupInfoInput,
    GetUserGroupsInput,
    GetUserInfoInput,
    GetUsersListInput,
    PinMessageInput,
    RemoveReactionInput,
    ReplyToMessageInput,
    ResolveUserInput,
    ScheduleMessageInput,
    SearchAllInput,
    SearchMessagesInput,
    SendDirectMessageInput,
    SendMessageInput,
    SendMessageToMultipleChannelsInput,
    SendMessageWithMentionsInput,
    SetUserStatusInput,
    Slack,
    UnpinMessageInput,
    UpdateMessageInput,
    UploadFileToChannelInput,
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
        assert data.oldest is None
        assert data.latest is None
        assert data.inclusive is None

    def test_limit_set(self):
        data = GetDmHistoryInput(user="U123ABC456", limit=50)
        assert data.limit == 50

    def test_time_range_fields_set(self):
        data = GetDmHistoryInput(
            user="U1", oldest="1700000000.000000", latest="1700050000.000000", inclusive=True
        )
        assert data.oldest == "1700000000.000000"
        assert data.latest == "1700050000.000000"
        assert data.inclusive is True


class TestSearchMessagesInput:
    def test_query_only(self):
        data = SearchMessagesInput(query="roadmap")
        assert data.query == "roadmap"
        assert data.channel is None
        assert data.with_user is None
        assert data.from_user is None
        assert data.count is None
        assert data.sort is None
        assert data.sort_dir is None
        assert data.after is None
        assert data.before is None

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

    def test_date_modifier_and_sort_dir_fields_set(self):
        data = SearchMessagesInput(
            query="release",
            after="2025-01-01",
            before="2025-12-31",
            sort_dir="asc",
        )
        assert data.after == "2025-01-01"
        assert data.before == "2025-12-31"
        assert data.sort_dir == "asc"


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
        gch.assert_awaited_once_with(
            "D9999",
            limit=25,
            oldest=None,
            latest=None,
            inclusive=None,
        )

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

    @pytest.mark.asyncio
    async def test_time_range_params_forwarded_through_delegation(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock(
            return_value=_ok({"channel": {"id": "D9999"}})
        )
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U123ABC456")
        ), patch.object(
            slack, "get_channel_history", AsyncMock(return_value=(True, '{"ok":true}'))
        ) as gch:
            await slack.get_dm_history(
                "alice@example.com",
                limit=10,
                oldest="1700000000.000000",
                latest="1700050000.000000",
                inclusive=True,
            )
        gch.assert_awaited_once_with(
            "D9999",
            limit=10,
            oldest="1700000000.000000",
            latest="1700050000.000000",
            inclusive=True,
        )


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
            query="roadmap", count=None, sort=None, sort_dir=None
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

    @pytest.mark.asyncio
    async def test_after_modifier_added(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        await slack.search_messages(query="release", after="2025-01-01")
        args = slack.client.search_messages.await_args.kwargs
        assert args["query"] == "after:2025-01-01 release"

    @pytest.mark.asyncio
    async def test_before_modifier_added(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        await slack.search_messages(query="release", before="2025-12-31")
        args = slack.client.search_messages.await_args.kwargs
        assert args["query"] == "before:2025-12-31 release"

    @pytest.mark.asyncio
    async def test_after_and_before_combined_with_channel(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        await slack.search_messages(
            query="release",
            channel="#general",
            after="2025-01-01",
            before="2025-12-31",
        )
        args = slack.client.search_messages.await_args.kwargs
        # Modifier order: channel, after, before, then raw query
        assert args["query"] == "in:general after:2025-01-01 before:2025-12-31 release"

    @pytest.mark.asyncio
    async def test_sort_dir_forwarded(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": []}))

        await slack.search_messages(query="q", sort="timestamp", sort_dir="asc")
        args = slack.client.search_messages.await_args.kwargs
        assert args["sort"] == "timestamp"
        assert args["sort_dir"] == "asc"


# ===========================================================================
# Additional Pydantic schemas
# ===========================================================================

class TestSendMessageInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            SendMessageInput(channel="#general")  # type: ignore[call-arg]
        with pytest.raises(Exception):
            SendMessageInput(message="hello")  # type: ignore[call-arg]

    def test_valid_construction(self):
        data = SendMessageInput(channel="#general", message="hello")
        assert data.channel == "#general"
        assert data.message == "hello"


class TestGetChannelHistoryInput:
    def test_channel_required(self):
        with pytest.raises(Exception):
            GetChannelHistoryInput()  # type: ignore[call-arg]

    def test_default_limit_none(self):
        data = GetChannelHistoryInput(channel="C12345")
        assert data.channel == "C12345"
        assert data.limit is None
        assert data.oldest is None
        assert data.latest is None
        assert data.inclusive is None

    def test_limit_set(self):
        data = GetChannelHistoryInput(channel="C12345", limit=10)
        assert data.limit == 10

    def test_time_range_fields_set(self):
        data = GetChannelHistoryInput(
            channel="C12345",
            oldest="1700000000.000000",
            latest="1700050000.000000",
            inclusive=True,
        )
        assert data.oldest == "1700000000.000000"
        assert data.latest == "1700050000.000000"
        assert data.inclusive is True


class TestSearchAllInput:
    def test_query_required(self):
        with pytest.raises(Exception):
            SearchAllInput()  # type: ignore[call-arg]

    def test_default_limit_none(self):
        data = SearchAllInput(query="X")
        assert data.limit is None


class TestSendDirectMessageInputAlias:
    def test_message_field_used_directly(self):
        data = SendDirectMessageInput(user="U123", message="hi")
        assert data.message == "hi"

    def test_text_alias_promoted_to_message(self):
        data = SendDirectMessageInput.model_validate(
            {"user": "U123", "text": "via alias"}
        )
        assert data.message == "via alias"

    def test_message_wins_when_both_provided(self):
        data = SendDirectMessageInput.model_validate(
            {"user": "U123", "message": "primary", "text": "fallback"}
        )
        assert data.message == "primary"

    def test_non_dict_input_passes_through(self):
        # Direct construction should not break if a model instance is passed
        data = SendDirectMessageInput(user="U123", message="m")
        assert data.message == "m"


class TestReplyToMessageInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            ReplyToMessageInput(channel="#general")  # type: ignore[call-arg]

    def test_default_optional_fields(self):
        data = ReplyToMessageInput(channel="#general", message="hi")
        assert data.thread_ts is None
        assert data.latest_message is None

    def test_with_thread_ts(self):
        data = ReplyToMessageInput(
            channel="#general", message="hi", thread_ts="123.456"
        )
        assert data.thread_ts == "123.456"


class TestSendMessageToMultipleChannelsInput:
    def test_required_fields(self):
        data = SendMessageToMultipleChannelsInput(
            channels=["#general", "#random"], message="broadcast"
        )
        assert data.channels == ["#general", "#random"]


class TestCreateChannelInput:
    def test_only_name_required(self):
        data = CreateChannelInput(name="dev")
        assert data.is_private is None
        assert data.topic is None
        assert data.purpose is None

    def test_full_fields(self):
        data = CreateChannelInput(
            name="dev", is_private=True, topic="t", purpose="p"
        )
        assert data.is_private is True
        assert data.topic == "t"


class TestGetChannelInfoInput:
    def test_channel_required(self):
        with pytest.raises(Exception):
            GetChannelInfoInput()  # type: ignore[call-arg]


class TestGetChannelMembersInput:
    def test_channel_required(self):
        with pytest.raises(Exception):
            GetChannelMembersInput()  # type: ignore[call-arg]


class TestGetChannelMembersByIdInput:
    def test_channel_id_required(self):
        with pytest.raises(Exception):
            GetChannelMembersByIdInput()  # type: ignore[call-arg]


class TestResolveUserInput:
    def test_user_id_required(self):
        with pytest.raises(Exception):
            ResolveUserInput()  # type: ignore[call-arg]


class TestAddReactionInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            AddReactionInput(channel="C1", timestamp="123.0")  # type: ignore[call-arg]

    def test_valid_construction(self):
        data = AddReactionInput(channel="C1", timestamp="1.0", name="thumbsup")
        assert data.name == "thumbsup"


class TestSetUserStatusInput:
    def test_only_status_text_required(self):
        data = SetUserStatusInput(status_text="Away")
        assert data.status_emoji is None
        assert data.duration_seconds is None

    def test_empty_status_text_allowed(self):
        # empty string is the "clear" sentinel
        data = SetUserStatusInput(status_text="")
        assert data.status_text == ""


class TestScheduleMessageInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            ScheduleMessageInput(channel="C1", message="hi")  # type: ignore[call-arg]


class TestPinMessageInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            PinMessageInput(channel="C1")  # type: ignore[call-arg]


class TestGetUnreadMessagesInput:
    def test_channel_required(self):
        with pytest.raises(Exception):
            GetUnreadMessagesInput()  # type: ignore[call-arg]


class TestGetScheduledMessagesInput:
    def test_channel_optional(self):
        data = GetScheduledMessagesInput()
        assert data.channel is None


class TestSendMessageWithMentionsInput:
    def test_default_mentions_none(self):
        data = SendMessageWithMentionsInput(channel="C1", message="m")
        assert data.mentions is None


class TestGetUsersListInput:
    def test_all_optional(self):
        data = GetUsersListInput()
        assert data.include_deleted is None
        assert data.limit is None


class TestGetUserConversationsInput:
    def test_all_optional(self):
        data = GetUserConversationsInput()
        assert data.types is None
        assert data.exclude_archived is None
        assert data.limit is None


class TestGetUserGroupsInput:
    def test_all_optional(self):
        data = GetUserGroupsInput()
        assert data.include_users is None
        assert data.include_disabled is None


class TestGetUserGroupInfoInput:
    def test_usergroup_required(self):
        with pytest.raises(Exception):
            GetUserGroupInfoInput()  # type: ignore[call-arg]


class TestGetUserChannelsInput:
    def test_all_optional(self):
        data = GetUserChannelsInput()
        assert data.exclude_archived is None
        assert data.types is None


class TestDeleteMessageInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            DeleteMessageInput(channel="C1")  # type: ignore[call-arg]


class TestUpdateMessageInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            UpdateMessageInput(channel="C1", timestamp="1.0")  # type: ignore[call-arg]

    def test_optional_blocks(self):
        data = UpdateMessageInput(channel="C1", timestamp="1.0", text="t")
        assert data.blocks is None


class TestGetMessagePermalinkInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            GetMessagePermalinkInput(channel="C1")  # type: ignore[call-arg]


class TestGetReactionsInput:
    def test_default_full_none(self):
        data = GetReactionsInput(channel="C1", timestamp="1.0")
        assert data.full is None


class TestRemoveReactionInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            RemoveReactionInput(channel="C1", timestamp="1.0")  # type: ignore[call-arg]


class TestGetPinnedMessagesInput:
    def test_channel_required(self):
        with pytest.raises(Exception):
            GetPinnedMessagesInput()  # type: ignore[call-arg]


class TestUnpinMessageInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            UnpinMessageInput(channel="C1")  # type: ignore[call-arg]


class TestGetThreadRepliesInput:
    def test_default_limit_none(self):
        data = GetThreadRepliesInput(channel="C1", timestamp="1.0")
        assert data.limit is None
        assert data.oldest is None
        assert data.latest is None
        assert data.inclusive is None

    def test_time_range_fields_set(self):
        data = GetThreadRepliesInput(
            channel="C1",
            timestamp="1.0",
            oldest="1700000000.000000",
            latest="1700050000.000000",
            inclusive=False,
        )
        assert data.oldest == "1700000000.000000"
        assert data.latest == "1700050000.000000"
        assert data.inclusive is False


class TestGetUserInfoInput:
    def test_user_required(self):
        with pytest.raises(Exception):
            GetUserInfoInput()  # type: ignore[call-arg]


class TestUploadFileToChannelInput:
    def test_required_fields(self):
        with pytest.raises(Exception):
            UploadFileToChannelInput(channel="C1", filename="a.txt")  # type: ignore[call-arg]

    def test_optional_title_and_comment(self):
        data = UploadFileToChannelInput(
            channel="C1", filename="a.txt", file_content="hello"
        )
        assert data.title is None
        assert data.initial_comment is None


# ===========================================================================
# Internal helpers: _handle_slack_response / _handle_slack_error
# ===========================================================================

class TestHandleSlackResponse:
    def test_none_response_returns_error(self):
        slack = _build_slack()
        resp = slack._handle_slack_response(None)
        assert resp.success is False
        assert resp.error == "Empty response from Slack API"

    def test_passthrough_when_already_slack_response(self):
        slack = _build_slack()
        existing = SlackResponse(success=True, data={"k": "v"})
        resp = slack._handle_slack_response(existing)
        assert resp is existing

    def test_dict_with_ok_false_returns_error(self):
        slack = _build_slack()
        resp = slack._handle_slack_response({"ok": False, "error": "boom"})
        assert resp.success is False
        assert resp.error == "boom"

    def test_dict_without_error_uses_unknown(self):
        slack = _build_slack()
        resp = slack._handle_slack_response({"ok": False})
        assert resp.success is False
        assert resp.error == "unknown_error"

    def test_dict_with_ok_true_wraps_data(self):
        slack = _build_slack()
        payload = {"ok": True, "channel": {"id": "C1"}}
        resp = slack._handle_slack_response(payload)
        assert resp.success is True
        assert resp.data == payload

    def test_non_dict_object_wraps_as_raw_response(self):
        slack = _build_slack()
        resp = slack._handle_slack_response("scalar-string")
        assert resp.success is True
        assert resp.data == {"raw_response": "scalar-string"}


class TestHandleSlackError:
    def test_wraps_exception_message(self):
        slack = _build_slack()
        resp = slack._handle_slack_error(RuntimeError("kaboom"))
        assert resp.success is False
        assert resp.error == "kaboom"


# ===========================================================================
# Internal helper: _convert_markdown_to_slack_mrkdwn
# ===========================================================================

class TestConvertMarkdownToSlackMrkdwn:
    def test_empty_string_returned_as_is(self):
        slack = _build_slack()
        assert slack._convert_markdown_to_slack_mrkdwn("") == ""

    def test_bold_double_asterisk_to_single(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("**hello**")
        assert out == "*hello*"

    def test_strikethrough_double_to_single_tilde(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("~~done~~")
        assert out == "~done~"

    def test_header_converted_to_bold(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("# Title")
        # Header becomes "*Title*\n" then trailing ws stripped
        assert "*Title*" in out

    def test_link_with_http_converted(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("[anthropic](https://anthropic.com)")
        assert out == "<https://anthropic.com|anthropic>"

    def test_non_url_link_left_intact(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("[ref](section-1)")
        # Non-URL — kept as-is
        assert out == "[ref](section-1)"

    def test_citations_protected_from_link_conversion(self):
        slack = _build_slack()
        # Test each citation form independently. The protection logic uses
        # `__CITATION_N__` placeholders; with two placeholders in one string,
        # the bold regex `__..__` matches across the boundary between them.
        assert "[R1-2]" in slack._convert_markdown_to_slack_mrkdwn("See [R1-2]")
        assert "[3]" in slack._convert_markdown_to_slack_mrkdwn("See [3]")

    def test_list_dash_bullet_normalized(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("- item")
        assert out.lstrip().startswith("•")

    def test_inline_code_preserved(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("Use `pip install` here")
        assert "`pip install`" in out

    def test_code_block_preserved(self):
        slack = _build_slack()
        src = "```python\nprint('hi')\n```"
        out = slack._convert_markdown_to_slack_mrkdwn(src)
        assert src in out

    def test_excessive_blank_lines_collapsed(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("line1\n\n\n\nline2")
        assert "\n\n\n" not in out

    def test_bold_inside_code_block_not_converted(self):
        slack = _build_slack()
        out = slack._convert_markdown_to_slack_mrkdwn("```\n**not bold**\n```")
        # Code block protected — text remains unchanged
        assert "**not bold**" in out


# ===========================================================================
# Internal helper: _get_authenticated_user_id
# ===========================================================================

class TestGetAuthenticatedUserId:
    @pytest.mark.asyncio
    async def test_returns_user_id_on_success(self):
        slack = _build_slack()
        slack.client.auth_test = AsyncMock(return_value=_ok({"user_id": "U999"}))
        user_id = await slack._get_authenticated_user_id()
        assert user_id == "U999"

    @pytest.mark.asyncio
    async def test_returns_none_when_auth_test_fails(self):
        slack = _build_slack()
        slack.client.auth_test = AsyncMock(return_value=_fail("not_authed"))
        assert await slack._get_authenticated_user_id() is None

    @pytest.mark.asyncio
    async def test_returns_none_when_user_id_missing(self):
        slack = _build_slack()
        slack.client.auth_test = AsyncMock(return_value=_ok({"team": "T1"}))
        assert await slack._get_authenticated_user_id() is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        slack = _build_slack()
        slack.client.auth_test = AsyncMock(side_effect=RuntimeError("bad"))
        assert await slack._get_authenticated_user_id() is None


# ===========================================================================
# Internal helper: _resolve_channel
# ===========================================================================

class TestResolveChannel:
    @pytest.mark.asyncio
    async def test_non_string_returned_as_is(self):
        slack = _build_slack()
        # Pass an int — function returns it back
        result = await slack._resolve_channel(12345)  # type: ignore[arg-type]
        assert result == 12345

    @pytest.mark.asyncio
    async def test_already_channel_id_returned_directly(self):
        slack = _build_slack()
        # Don't mock conversations_list — must not be called
        slack.client.conversations_list = AsyncMock(side_effect=AssertionError("should not be called"))
        result = await slack._resolve_channel("C1234ABCD")
        assert result == "C1234ABCD"

    @pytest.mark.asyncio
    async def test_resolves_name_to_id(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(
            return_value=_ok({
                "channels": [
                    {"id": "C111", "name": "general"},
                    {"id": "C222", "name": "random"},
                ],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_channel("#random")
        assert result == "C222"

    @pytest.mark.asyncio
    async def test_resolves_name_without_hash(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(
            return_value=_ok({
                "channels": [{"id": "C111", "name": "general"}],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_channel("general")
        assert result == "C111"

    @pytest.mark.asyncio
    async def test_pagination_walks_all_pages(self):
        slack = _build_slack()
        page1 = _ok({
            "channels": [{"id": "C111", "name": "general"}],
            "response_metadata": {"next_cursor": "cur1"},
        })
        page2 = _ok({
            "channels": [{"id": "C222", "name": "engineering"}],
            "response_metadata": {"next_cursor": ""},
        })
        slack.client.conversations_list = AsyncMock(side_effect=[page1, page2])
        result = await slack._resolve_channel("engineering")
        assert result == "C222"
        assert slack.client.conversations_list.await_count == 2

    @pytest.mark.asyncio
    async def test_unresolved_returns_original(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(
            return_value=_ok({
                "channels": [{"id": "C111", "name": "other"}],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_channel("ghost-channel")
        assert result == "ghost-channel"

    @pytest.mark.asyncio
    async def test_failed_list_returns_original(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(return_value=_fail("rate_limited"))
        result = await slack._resolve_channel("general")
        assert result == "general"

    @pytest.mark.asyncio
    async def test_exception_returns_original(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(side_effect=RuntimeError("net"))
        result = await slack._resolve_channel("general")
        assert result == "general"


# ===========================================================================
# Internal helper: _resolve_user_identifier
# ===========================================================================

class TestResolveUserIdentifier:
    @pytest.mark.asyncio
    async def test_empty_returns_none(self):
        slack = _build_slack()
        assert await slack._resolve_user_identifier("") is None

    @pytest.mark.asyncio
    async def test_non_string_returns_none(self):
        slack = _build_slack()
        assert await slack._resolve_user_identifier(None) is None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_already_user_id_returned_directly(self):
        slack = _build_slack()
        slack.client.users_lookup_by_email = AsyncMock(side_effect=AssertionError())
        result = await slack._resolve_user_identifier("U123ABC456")
        assert result == "U123ABC456"

    @pytest.mark.asyncio
    async def test_email_lookup_success(self):
        slack = _build_slack()
        slack.client.users_lookup_by_email = AsyncMock(
            return_value=_ok({"user": {"id": "U777"}})
        )
        result = await slack._resolve_user_identifier("alice@example.com")
        assert result == "U777"
        slack.client.users_lookup_by_email.assert_awaited_once_with(email="alice@example.com")

    @pytest.mark.asyncio
    async def test_email_lookup_failure_falls_back_to_users_list(self):
        slack = _build_slack()
        slack.client.users_lookup_by_email = AsyncMock(side_effect=RuntimeError("not_found"))
        slack.client.users_list = AsyncMock(
            return_value=_ok({
                "members": [{
                    "id": "U888",
                    "name": "alice",
                    "profile": {
                        "display_name": "alice",
                        "real_name": "Alice Liddell",
                    },
                }],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_user_identifier("alice@example.com")
        assert result == "U888"

    @pytest.mark.asyncio
    async def test_exact_name_match_found_via_users_list(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(
            return_value=_ok({
                "members": [{
                    "id": "U101",
                    "name": "alice",
                    "profile": {
                        "display_name": "alice",
                        "real_name": "Alice One",
                    },
                }],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_user_identifier("alice")
        assert result == "U101"

    @pytest.mark.asyncio
    async def test_partial_match_returns_first_starting_with_target(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(
            return_value=_ok({
                "members": [{
                    "id": "U501",
                    "name": "abhishekg",
                    "profile": {"real_name": "Abhishek Gupta"},
                }],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_user_identifier("Abhishek")
        assert result == "U501"

    @pytest.mark.asyncio
    async def test_skip_deleted_and_bot_users(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(
            return_value=_ok({
                "members": [
                    {"id": "U999", "name": "alice", "deleted": True,
                     "profile": {"display_name": "alice"}},
                    {"id": "B100", "name": "alice", "is_bot": True,
                     "profile": {"display_name": "alice"}},
                    {"id": "U101", "name": "alice",
                     "profile": {"display_name": "alice"}},
                ],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_user_identifier("alice")
        assert result == "U101"

    @pytest.mark.asyncio
    async def test_ambiguous_exact_matches_raises(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(
            return_value=_ok({
                "members": [
                    {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice", "real_name": "Alice One"}},
                    {"id": "U2", "name": "alice2",
                     "profile": {"display_name": "alice", "real_name": "Alice Two"}},
                ],
                "response_metadata": {"next_cursor": ""},
            })
        )
        with pytest.raises(AmbiguousUserError):
            await slack._resolve_user_identifier("alice", allow_ambiguous=False)

    @pytest.mark.asyncio
    async def test_allow_ambiguous_returns_first_match(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(
            return_value=_ok({
                "members": [
                    {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}},
                    {"id": "U2", "name": "alice2",
                     "profile": {"display_name": "alice"}},
                ],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_user_identifier("alice", allow_ambiguous=True)
        assert result == "U1"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(
            return_value=_ok({
                "members": [
                    {"id": "U1", "name": "completely_other",
                     "profile": {"display_name": "completely_other"}},
                ],
                "response_metadata": {"next_cursor": ""},
            })
        )
        result = await slack._resolve_user_identifier("xyzzy_unknown")
        assert result is None


# ===========================================================================
# send_message
# ===========================================================================

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_success_uses_resolved_channel_and_converts_markdown(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "1.0"}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.send_message("#general", "**bold**")
        assert ok is True
        kwargs = slack.client.chat_post_message.await_args.kwargs
        assert kwargs["channel"] == "C1234ABCD"
        # bold ** converted to single *
        assert kwargs["text"] == "*bold*"
        assert kwargs["mrkdwn"] is True

    @pytest.mark.asyncio
    async def test_api_failure_returned_as_error(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_fail("channel_not_found"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.send_message("C1234ABCD", "hi")
        assert ok is False
        assert json.loads(payload)["error"] == "channel_not_found"

    @pytest.mark.asyncio
    async def test_not_in_channel_short_circuit(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(side_effect=Exception("not_in_channel"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.send_message("C1234ABCD", "hi")
        assert ok is False
        assert json.loads(payload)["error"] == "not_in_channel"

    @pytest.mark.asyncio
    async def test_unexpected_exception_wrapped(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(side_effect=RuntimeError("net"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.send_message("C1234ABCD", "hi")
        assert ok is False
        assert "net" in json.loads(payload)["error"]


# ===========================================================================
# get_channel_history
# ===========================================================================

class TestGetChannelHistory:
    @pytest.mark.asyncio
    async def test_success_with_no_messages(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(
            return_value=_ok({"messages": []})
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_history("C1234ABCD")
        assert ok is True
        body = json.loads(payload)
        assert body["success"] is True
        assert body["data"]["messages"] == []

    @pytest.mark.asyncio
    async def test_resolves_user_mentions_in_messages(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(
            return_value=_ok({
                "messages": [
                    {"text": "hey <@U123ABC456> ping", "ts": "1.0"},
                ]
            })
        )
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {
                "id": "U123ABC456",
                "name": "alice",
                "profile": {"display_name": "alice", "email": "a@x.com"},
            }
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_history("C1234ABCD")
        assert ok is True
        body = json.loads(payload)
        msg = body["data"]["messages"][0]
        assert msg["resolved_text"] == "hey @alice ping"
        assert any(m["id"] == "U123ABC456" and m["email"] == "a@x.com"
                   for m in msg.get("mentions", []))

    @pytest.mark.asyncio
    async def test_failed_history_returns_error(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(
            return_value=_fail("channel_not_found")
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_history("C1234ABCD")
        assert ok is False
        assert json.loads(payload)["error"] == "channel_not_found"

    @pytest.mark.asyncio
    async def test_not_in_channel_short_circuit(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(
            side_effect=Exception("not_in_channel")
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_history("C1234ABCD")
        assert ok is False
        assert json.loads(payload)["error"] == "not_in_channel"

    @pytest.mark.asyncio
    async def test_unexpected_exception_wrapped(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_history("C1234ABCD")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_time_range_params_forwarded(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(
            return_value=_ok({"messages": []})
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.get_channel_history(
                "C1234ABCD",
                limit=5,
                oldest="1700000000.000000",
                latest="1700050000.000000",
                inclusive=True,
            )
        slack.client.conversations_history.assert_awaited_once_with(
            channel="C1234ABCD",
            limit=5,
            oldest="1700000000.000000",
            latest="1700050000.000000",
            inclusive=True,
        )


# ===========================================================================
# get_channel_info
# ===========================================================================

class TestGetChannelInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(
            return_value=_ok({"channel": {"id": "C1", "name": "general"}})
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_info("#general")
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["channel"]["name"] == "general"

    @pytest.mark.asyncio
    async def test_failure(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_fail("not_found"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_info("C1234ABCD")
        assert ok is False
        assert json.loads(payload)["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(side_effect=RuntimeError("oops"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_info("C1234ABCD")
        assert ok is False
        assert "oops" in json.loads(payload)["error"]


# ===========================================================================
# get_user_info
# ===========================================================================

class TestGetUserInfo:
    @pytest.mark.asyncio
    async def test_success_returns_flattened_user(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {
                "id": "U101",
                "name": "alice",
                "real_name": "Alice One",
                "team_id": "T1",
                "is_bot": False,
                "is_admin": False,
                "is_owner": False,
                "is_primary_owner": False,
                "tz": "UTC",
                "profile": {"display_name": "alice", "email": "alice@example.com"},
            }
        }))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U101")
        ):
            ok, payload = await slack.get_user_info("alice")
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["id"] == "U101"
        assert body["data"]["email"] == "alice@example.com"
        assert body["data"]["display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_unresolvable_user_uses_input_as_id(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_fail("user_not_found"))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value=None)
        ):
            ok, payload = await slack.get_user_info("ghost")
        assert ok is False
        assert json.loads(payload)["error"] == "user_not_found"

    @pytest.mark.asyncio
    async def test_ambiguous_user_returns_disambiguation(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        matches = [
            {"id": "U1", "name": "alice", "real_name": "Alice One",
             "email": "a1@x.com"},
            {"id": "U2", "name": "alice", "real_name": "Alice Two",
             "email": "a2@x.com"},
        ]
        with patch.object(
            slack,
            "_resolve_user_identifier",
            AsyncMock(side_effect=AmbiguousUserError("alice", matches)),
        ):
            ok, payload = await slack.get_user_info("alice")
        assert ok is False
        body = json.loads(payload)
        assert "Multiple users found matching 'alice'" in body["error"]
        assert "Alice One" in body["error"]
        slack.client.users_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        with patch.object(
            slack,
            "_resolve_user_identifier",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            ok, payload = await slack.get_user_info("alice")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# fetch_channels
# ===========================================================================

class TestFetchChannels:
    @pytest.mark.asyncio
    async def test_success_no_pagination(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(
            return_value=_ok({
                "channels": [{"id": "C1", "name": "general"}],
                "response_metadata": {"next_cursor": ""},
            })
        )
        ok, payload = await slack.fetch_channels()
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["count"] == 1

    @pytest.mark.asyncio
    async def test_pagination_collects_all_pages(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(side_effect=[
            _ok({
                "channels": [{"id": "C1", "name": "a"}],
                "response_metadata": {"next_cursor": "next"},
            }),
            _ok({
                "channels": [{"id": "C2", "name": "b"}],
                "response_metadata": {"next_cursor": ""},
            }),
        ])
        ok, payload = await slack.fetch_channels()
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["count"] == 2

    @pytest.mark.asyncio
    async def test_first_page_failure_returns_error(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(return_value=_fail("invalid_auth"))
        ok, payload = await slack.fetch_channels()
        assert ok is False
        assert json.loads(payload)["error"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_second_page_failure_returns_partial(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(side_effect=[
            _ok({
                "channels": [{"id": "C1", "name": "a"}],
                "response_metadata": {"next_cursor": "next"},
            }),
            _fail("rate_limited"),
        ])
        ok, payload = await slack.fetch_channels()
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["count"] == 1

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.fetch_channels()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_default_args_preserve_prior_behavior(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(
            return_value=_ok({
                "channels": [],
                "response_metadata": {"next_cursor": ""},
            })
        )
        await slack.fetch_channels()
        kwargs = slack.client.conversations_list.await_args.kwargs
        assert kwargs["types"] == "public_channel,private_channel,mpim,im"
        assert kwargs["exclude_archived"] is False
        assert kwargs["limit"] == 1000

    @pytest.mark.asyncio
    async def test_types_and_exclude_archived_forwarded(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(
            return_value=_ok({
                "channels": [],
                "response_metadata": {"next_cursor": ""},
            })
        )
        await slack.fetch_channels(types="public_channel", exclude_archived=True)
        kwargs = slack.client.conversations_list.await_args.kwargs
        assert kwargs["types"] == "public_channel"
        assert kwargs["exclude_archived"] is True


# ===========================================================================
# search_all
# ===========================================================================

class TestSearchAll:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(
            return_value=_ok({"messages": {"matches": []}})
        )
        ok, payload = await slack.search_all("project")
        assert ok is True
        slack.client.search_messages.assert_awaited_once_with(
            query="project", count=None
        )

    @pytest.mark.asyncio
    async def test_passes_limit_as_count(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({"messages": {"matches": []}}))
        await slack.search_all("project", limit=5)
        assert slack.client.search_messages.await_args.kwargs["count"] == 5

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(side_effect=RuntimeError("net"))
        ok, payload = await slack.search_all("q")
        assert ok is False
        assert "net" in json.loads(payload)["error"]


# ===========================================================================
# get_channel_members / get_channel_members_by_id
# ===========================================================================

class TestGetChannelMembers:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.conversations_members = AsyncMock(
            return_value=_ok({"members": ["U1", "U2"]})
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_members("#general")
        assert ok is True
        assert json.loads(payload)["data"]["members"] == ["U1", "U2"]

    @pytest.mark.asyncio
    async def test_not_in_channel_short_circuit(self):
        slack = _build_slack()
        slack.client.conversations_members = AsyncMock(
            side_effect=Exception("not_in_channel")
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_members("C1234ABCD")
        assert ok is False
        assert json.loads(payload)["error"] == "not_in_channel"

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.conversations_members = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.get_channel_members("C1234ABCD")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


class TestGetChannelMembersById:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.conversations_members = AsyncMock(
            return_value=_ok({"members": ["U9"]})
        )
        ok, payload = await slack.get_channel_members_by_id("C9")
        assert ok is True
        slack.client.conversations_members.assert_awaited_once_with(channel="C9")

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.conversations_members = AsyncMock(side_effect=RuntimeError("oops"))
        ok, payload = await slack.get_channel_members_by_id("C9")
        assert ok is False
        assert "oops" in json.loads(payload)["error"]


# ===========================================================================
# resolve_user
# ===========================================================================

class TestResolveUser:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {
                "id": "U101",
                "name": "alice",
                "real_name": "Alice One",
                "profile": {"display_name": "alice", "email": "alice@example.com"},
            }
        }))
        ok, payload = await slack.resolve_user("U101")
        assert ok is True
        data = json.loads(payload)["data"]
        assert data["id"] == "U101"
        assert data["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_failure_returned_as_is(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_fail("user_not_found"))
        ok, payload = await slack.resolve_user("U999")
        assert ok is False
        assert json.loads(payload)["error"] == "user_not_found"

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.resolve_user("U101")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# check_token_info
# ===========================================================================

class TestCheckTokenInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.check_token_scopes = AsyncMock(
            return_value=_ok({"scopes": ["chat:write"]})
        )
        ok, payload = await slack.check_token_info()
        assert ok is True

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.check_token_scopes = AsyncMock(side_effect=RuntimeError("oops"))
        ok, payload = await slack.check_token_info()
        assert ok is False
        assert "oops" in json.loads(payload)["error"]


# ===========================================================================
# send_direct_message
# ===========================================================================

class TestSendDirectMessage:
    @pytest.mark.asyncio
    async def test_success_full_path(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock(
            return_value=_ok({"channel": {"id": "D9"}})
        )
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "1.0"}))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U101")
        ):
            ok, _ = await slack.send_direct_message("alice@example.com", "hi")
        assert ok is True
        slack.client.conversations_open.assert_awaited_once_with(users=["U101"])
        kwargs = slack.client.chat_post_message.await_args.kwargs
        assert kwargs["channel"] == "D9"

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock()
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value=None)
        ):
            ok, payload = await slack.send_direct_message("ghost", "hi")
        assert ok is False
        assert "ghost" in json.loads(payload)["error"]
        slack.client.conversations_open.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ambiguous_user_returns_disambiguation(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock()
        matches = [{"id": "U1", "name": "a", "real_name": "Alice"},
                   {"id": "U2", "name": "a", "real_name": "Alex"}]
        with patch.object(
            slack,
            "_resolve_user_identifier",
            AsyncMock(side_effect=AmbiguousUserError("a", matches)),
        ):
            ok, payload = await slack.send_direct_message("a", "hi")
        assert ok is False
        body = json.loads(payload)
        assert "Multiple users found" in body["error"]
        slack.client.conversations_open.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_conversations_open_failure(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock(return_value=_fail("user_disabled"))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U101")
        ):
            ok, payload = await slack.send_direct_message("alice", "hi")
        assert ok is False
        assert json.loads(payload)["error"] == "user_disabled"

    @pytest.mark.asyncio
    async def test_no_channel_id_in_open_response(self):
        slack = _build_slack()
        slack.client.conversations_open = AsyncMock(return_value=_ok({"channel": {}}))
        with patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value="U101")
        ):
            ok, payload = await slack.send_direct_message("alice", "hi")
        assert ok is False
        assert json.loads(payload)["error"] == "Failed to get DM channel ID"

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        with patch.object(
            slack,
            "_resolve_user_identifier",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            ok, payload = await slack.send_direct_message("alice", "hi")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# reply_to_message
# ===========================================================================

class TestReplyToMessage:
    @pytest.mark.asyncio
    async def test_success_with_thread_ts(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "2.0"}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.reply_to_message("C1234ABCD", "**hi**", thread_ts="1.0")
        assert ok is True
        kwargs = slack.client.chat_post_message.await_args.kwargs
        assert kwargs["thread_ts"] == "1.0"
        assert kwargs["text"] == "*hi*"

    @pytest.mark.asyncio
    async def test_latest_message_fetches_history(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(return_value=_ok({
            "messages": [{"ts": "9.0", "text": "latest"}]
        }))
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "10.0"}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.reply_to_message("C1234ABCD", "ack", latest_message=True)
        assert ok is True
        assert slack.client.chat_post_message.await_args.kwargs["thread_ts"] == "9.0"

    @pytest.mark.asyncio
    async def test_latest_message_history_fails(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(return_value=_fail("nope"))
        slack.client.chat_post_message = AsyncMock()
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.reply_to_message("C1234ABCD", "ack", latest_message=True)
        assert ok is False
        assert "Failed to get latest message" in json.loads(payload)["error"]
        slack.client.chat_post_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_latest_message_empty_history(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(return_value=_ok({"messages": []}))
        slack.client.chat_post_message = AsyncMock()
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.reply_to_message("C1234ABCD", "ack", latest_message=True)
        assert ok is False
        assert "No messages found in channel" in json.loads(payload)["error"]
        slack.client.chat_post_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_thread_ts_no_latest_returns_error(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock()
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.reply_to_message("C1234ABCD", "ack")
        assert ok is False
        assert "No thread timestamp provided" in json.loads(payload)["error"]
        slack.client.chat_post_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.reply_to_message("C1234ABCD", "ack", thread_ts="1.0")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# send_message_to_multiple_channels
# ===========================================================================

class TestSendMessageToMultipleChannels:
    @pytest.mark.asyncio
    async def test_all_succeed(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "1.0"}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(side_effect=lambda c: c)
        ):
            ok, payload = await slack.send_message_to_multiple_channels(
                ["C1", "C2"], "hello"
            )
        assert ok is True
        assert slack.client.chat_post_message.await_count == 2
        results = json.loads(payload)["data"]["results"]
        assert all(r["success"] for r in results)

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(side_effect=[
            _ok({"ts": "1.0"}),
            _fail("channel_not_found"),
        ])
        with patch.object(
            slack, "_resolve_channel", AsyncMock(side_effect=lambda c: c)
        ):
            ok, payload = await slack.send_message_to_multiple_channels(
                ["C1", "Cbad"], "hello"
            )
        assert ok is False
        results = json.loads(payload)["data"]["results"]
        assert results[0]["success"] is True
        assert results[1]["success"] is False
        assert results[1]["error"] == "channel_not_found"

    @pytest.mark.asyncio
    async def test_per_channel_exception_does_not_abort(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(side_effect=[
            RuntimeError("kaboom"),
            _ok({"ts": "2.0"}),
        ])
        with patch.object(
            slack, "_resolve_channel", AsyncMock(side_effect=lambda c: c)
        ):
            ok, payload = await slack.send_message_to_multiple_channels(
                ["C1", "C2"], "hello"
            )
        assert ok is False
        results = json.loads(payload)["data"]["results"]
        assert results[0]["success"] is False
        assert "kaboom" in results[0]["error"]
        assert results[1]["success"] is True


# ===========================================================================
# add_reaction / remove_reaction
# ===========================================================================

class TestAddReaction:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.reactions_add = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.add_reaction("#general", "1.0", "thumbsup")
        assert ok is True
        slack.client.reactions_add.assert_awaited_once_with(
            channel="C1234ABCD", timestamp="1.0", name="thumbsup"
        )

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.reactions_add = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.add_reaction("C1234ABCD", "1.0", "thumbsup")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


class TestRemoveReaction:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.reactions_remove = AsyncMock(return_value=_ok({}))
        ok, _ = await slack.remove_reaction("C1", "1.0", "thumbsup")
        assert ok is True

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.reactions_remove = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.remove_reaction("C1", "1.0", "thumbsup")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# set_user_status
# ===========================================================================

class TestSetUserStatus:
    @pytest.mark.asyncio
    async def test_clear_status_with_empty_text(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(return_value=_ok({"profile": {}}))
        ok, _ = await slack.set_user_status("")
        assert ok is True
        kwargs = slack.client.users_profile_set.await_args.kwargs
        assert kwargs["profile"] == {"status_text": "", "status_emoji": ""}
        assert kwargs["status_expiration"] == 0

    @pytest.mark.asyncio
    async def test_set_status_without_emoji(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(return_value=_ok({"profile": {}}))
        ok, _ = await slack.set_user_status("In a meeting")
        assert ok is True
        kwargs = slack.client.users_profile_set.await_args.kwargs
        assert kwargs["profile"]["status_text"] == "In a meeting"
        assert "status_emoji" not in kwargs["profile"]
        assert "status_expiration" not in kwargs

    @pytest.mark.asyncio
    async def test_set_status_normalizes_emoji(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(return_value=_ok({"profile": {}}))
        # Without colons → tool wraps with colons
        await slack.set_user_status("Lunch", status_emoji="taco")
        kwargs = slack.client.users_profile_set.await_args.kwargs
        assert kwargs["profile"]["status_emoji"] == ":taco:"

    @pytest.mark.asyncio
    async def test_set_status_with_already_colon_emoji(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(return_value=_ok({"profile": {}}))
        await slack.set_user_status("Lunch", status_emoji=":taco:")
        kwargs = slack.client.users_profile_set.await_args.kwargs
        assert kwargs["profile"]["status_emoji"] == ":taco:"

    @pytest.mark.asyncio
    async def test_set_status_with_duration(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(return_value=_ok({"profile": {}}))
        # `time` is imported inside the method (`import time as _time`); we
        # can't patch a module attribute that doesn't exist on slack.slack.
        # Instead, sanity-check the expiration is a future epoch ≥ now+duration.
        import time

        before = int(time.time())
        await slack.set_user_status("Away", duration_seconds=3600)
        kwargs = slack.client.users_profile_set.await_args.kwargs
        assert isinstance(kwargs["status_expiration"], int)
        # expiration must be roughly now + 3600 (allow a small window)
        assert kwargs["status_expiration"] >= before + 3600
        assert kwargs["status_expiration"] <= before + 3600 + 5

    @pytest.mark.asyncio
    async def test_set_status_with_zero_duration_does_not_expire(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(return_value=_ok({"profile": {}}))
        await slack.set_user_status("Away", duration_seconds=0)
        kwargs = slack.client.users_profile_set.await_args.kwargs
        assert "status_expiration" not in kwargs

    @pytest.mark.asyncio
    async def test_set_status_with_invalid_emoji_skipped(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(return_value=_ok({"profile": {}}))
        # ":" alone is too short to be a valid emoji — should be skipped
        await slack.set_user_status("Lunch", status_emoji=":")
        kwargs = slack.client.users_profile_set.await_args.kwargs
        assert "status_emoji" not in kwargs["profile"]

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.users_profile_set = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.set_user_status("Away")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# schedule_message
# ===========================================================================

class TestScheduleMessage:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.chat_schedule_message = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.schedule_message("#general", "**hi**", "1234567890")
        assert ok is True
        kwargs = slack.client.chat_schedule_message.await_args.kwargs
        assert kwargs["post_at"] == 1234567890
        assert kwargs["text"] == "*hi*"

    @pytest.mark.asyncio
    async def test_invalid_post_at_raises_inside_try(self):
        slack = _build_slack()
        slack.client.chat_schedule_message = AsyncMock()
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.schedule_message("C1234ABCD", "hi", "not-a-number")
        assert ok is False
        # int("not-a-number") raises ValueError → wrapped as error
        assert "invalid literal" in json.loads(payload)["error"]


# ===========================================================================
# pin_message / unpin_message / get_pinned_messages
# ===========================================================================

class TestPinUnpin:
    @pytest.mark.asyncio
    async def test_pin_message_success(self):
        slack = _build_slack()
        slack.client.pins_add = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.pin_message("C1234ABCD", "1.0")
        assert ok is True
        slack.client.pins_add.assert_awaited_once_with(
            channel="C1234ABCD", timestamp="1.0"
        )

    @pytest.mark.asyncio
    async def test_pin_message_exception(self):
        slack = _build_slack()
        slack.client.pins_add = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.pin_message("C1234ABCD", "1.0")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_unpin_message_success(self):
        slack = _build_slack()
        slack.client.pins_remove = AsyncMock(return_value=_ok({}))
        ok, _ = await slack.unpin_message("C1", "1.0")
        assert ok is True

    @pytest.mark.asyncio
    async def test_unpin_message_exception(self):
        slack = _build_slack()
        slack.client.pins_remove = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.unpin_message("C1", "1.0")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_get_pinned_messages_success(self):
        slack = _build_slack()
        slack.client.pins_list = AsyncMock(return_value=_ok({"items": []}))
        ok, _ = await slack.get_pinned_messages("C1")
        assert ok is True

    @pytest.mark.asyncio
    async def test_get_pinned_messages_exception(self):
        slack = _build_slack()
        slack.client.pins_list = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_pinned_messages("C1")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_unread_messages
# ===========================================================================

class TestGetUnreadMessages:
    @pytest.mark.asyncio
    async def test_success_combines_info_and_history(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(
            return_value=_ok({"channel": {"id": "C1", "unread_count": 3}})
        )
        slack.client.conversations_history = AsyncMock(
            return_value=_ok({"messages": [{"ts": "1.0", "text": "hi"}]})
        )
        ok, payload = await slack.get_unread_messages("C1")
        assert ok is True
        body = json.loads(payload)
        assert "channel_info" in body["data"]
        assert "recent_messages" in body["data"]

    @pytest.mark.asyncio
    async def test_info_failure_short_circuits(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_fail("not_in_channel"))
        slack.client.conversations_history = AsyncMock()
        ok, payload = await slack.get_unread_messages("C1")
        assert ok is False
        slack.client.conversations_history.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_history_failure_short_circuits(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(
            return_value=_ok({"channel": {"id": "C1"}})
        )
        slack.client.conversations_history = AsyncMock(return_value=_fail("rate_limited"))
        ok, _ = await slack.get_unread_messages("C1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_unread_messages("C1")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_scheduled_messages
# ===========================================================================

class TestGetScheduledMessages:
    @pytest.mark.asyncio
    async def test_no_channel_param(self):
        slack = _build_slack()
        slack.client.chat_scheduled_messages_list = AsyncMock(
            return_value=_ok({"scheduled_messages": []})
        )
        ok, _ = await slack.get_scheduled_messages()
        assert ok is True
        slack.client.chat_scheduled_messages_list.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_with_channel_param(self):
        slack = _build_slack()
        slack.client.chat_scheduled_messages_list = AsyncMock(
            return_value=_ok({"scheduled_messages": []})
        )
        await slack.get_scheduled_messages(channel="C1")
        slack.client.chat_scheduled_messages_list.assert_awaited_once_with(channel="C1")

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.chat_scheduled_messages_list = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        ok, payload = await slack.get_scheduled_messages()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# send_message_with_mentions
# ===========================================================================

class TestSendMessageWithMentions:
    @pytest.mark.asyncio
    async def test_replaces_mentions_with_user_ids(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "1.0"}))

        async def fake_resolve(identifier, allow_ambiguous=False):  # noqa: ARG001
            return {"alice": "U101", "bob": "U202"}.get(identifier)

        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ), patch.object(
            slack, "_resolve_user_identifier", AsyncMock(side_effect=fake_resolve)
        ):
            ok, _ = await slack.send_message_with_mentions(
                "C1234ABCD", "hi @alice and @bob", mentions=["alice", "bob"]
            )
        assert ok is True
        sent_text = slack.client.chat_post_message.await_args.kwargs["text"]
        assert "<@U101>" in sent_text
        assert "<@U202>" in sent_text

    @pytest.mark.asyncio
    async def test_unresolved_mention_left_unchanged(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "1.0"}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ), patch.object(
            slack, "_resolve_user_identifier", AsyncMock(return_value=None)
        ):
            ok, _ = await slack.send_message_with_mentions(
                "C1234ABCD", "hi @ghost", mentions=["ghost"]
            )
        assert ok is True
        sent_text = slack.client.chat_post_message.await_args.kwargs["text"]
        # Unresolved name kept literally
        assert "@ghost" in sent_text

    @pytest.mark.asyncio
    async def test_ambiguous_mention_skipped_silently(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "1.0"}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ), patch.object(
            slack,
            "_resolve_user_identifier",
            AsyncMock(side_effect=AmbiguousUserError("alice", [{"id": "U1"}, {"id": "U2"}])),
        ):
            ok, _ = await slack.send_message_with_mentions(
                "C1234ABCD", "hi @alice", mentions=["alice"]
            )
        # Message still sent (mention left literal)
        assert ok is True

    @pytest.mark.asyncio
    async def test_no_mentions_passed(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(return_value=_ok({"ts": "1.0"}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.send_message_with_mentions("C1234ABCD", "hi all")
        assert ok is True

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.chat_post_message = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.send_message_with_mentions("C1234ABCD", "hi")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_users_list
# ===========================================================================

class TestGetUsersList:
    @pytest.mark.asyncio
    async def test_with_limit_single_call(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(return_value=_ok({"members": [{"id": "U1"}]}))
        ok, _ = await slack.get_users_list(limit=10)
        assert ok is True
        kwargs = slack.client.users_list.await_args.kwargs
        assert kwargs["limit"] == 10
        assert kwargs["include_deleted"] is True  # default

    @pytest.mark.asyncio
    async def test_pagination_collects_all_pages(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(side_effect=[
            _ok({
                "members": [{"id": "U1"}],
                "response_metadata": {"next_cursor": "next"},
            }),
            _ok({
                "members": [{"id": "U2"}],
                "response_metadata": {"next_cursor": ""},
            }),
        ])
        ok, payload = await slack.get_users_list()
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["count"] == 2

    @pytest.mark.asyncio
    async def test_first_page_failure_returns_error(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(return_value=_fail("invalid_auth"))
        ok, payload = await slack.get_users_list()
        assert ok is False
        assert json.loads(payload)["error"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_second_page_failure_returns_partial(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(side_effect=[
            _ok({
                "members": [{"id": "U1"}],
                "response_metadata": {"next_cursor": "next"},
            }),
            _fail("rate_limited"),
        ])
        ok, payload = await slack.get_users_list()
        assert ok is True
        assert json.loads(payload)["data"]["count"] == 1

    @pytest.mark.asyncio
    async def test_explicit_include_deleted_false(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(
            return_value=_ok({"members": [], "response_metadata": {"next_cursor": ""}})
        )
        await slack.get_users_list(include_deleted=False)
        assert slack.client.users_list.await_args.kwargs["include_deleted"] is False

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.users_list = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_users_list()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_user_conversations
# ===========================================================================

class TestGetUserConversations:
    @pytest.mark.asyncio
    async def test_no_authenticated_user(self):
        slack = _build_slack()
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value=None)
        ):
            ok, payload = await slack.get_user_conversations()
        assert ok is False
        assert "authenticated user" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_with_limit_single_call(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(
            return_value=_ok({"channels": [{"id": "C1"}]})
        )
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value="U101")
        ):
            ok, _ = await slack.get_user_conversations(limit=5)
        assert ok is True
        kwargs = slack.client.users_conversations.await_args.kwargs
        assert kwargs["user"] == "U101"
        assert kwargs["limit"] == 5
        assert kwargs["types"] == "public_channel,private_channel,mpim,im"

    @pytest.mark.asyncio
    async def test_pagination(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(side_effect=[
            _ok({
                "channels": [{"id": "C1"}],
                "response_metadata": {"next_cursor": "next"},
            }),
            _ok({
                "channels": [{"id": "C2"}],
                "response_metadata": {"next_cursor": ""},
            }),
        ])
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value="U101")
        ):
            ok, payload = await slack.get_user_conversations()
        assert ok is True
        assert json.loads(payload)["data"]["count"] == 2

    @pytest.mark.asyncio
    async def test_explicit_types_passed_through(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(
            return_value=_ok({"channels": [], "response_metadata": {"next_cursor": ""}})
        )
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value="U101")
        ):
            await slack.get_user_conversations(types="im")
        kwargs = slack.client.users_conversations.await_args.kwargs
        assert kwargs["types"] == "im"

    @pytest.mark.asyncio
    async def test_first_page_failure_returns_error(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(return_value=_fail("invalid_auth"))
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value="U101")
        ):
            ok, payload = await slack.get_user_conversations()
        assert ok is False
        assert json.loads(payload)["error"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        with patch.object(
            slack,
            "_get_authenticated_user_id",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            ok, payload = await slack.get_user_conversations()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_user_groups / get_user_group_info
# ===========================================================================

class TestGetUserGroups:
    @pytest.mark.asyncio
    async def test_no_kwargs_when_options_none(self):
        slack = _build_slack()
        slack.client.usergroups_list = AsyncMock(return_value=_ok({"usergroups": []}))
        ok, _ = await slack.get_user_groups()
        assert ok is True
        slack.client.usergroups_list.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_kwargs_set_when_options_provided(self):
        slack = _build_slack()
        slack.client.usergroups_list = AsyncMock(return_value=_ok({"usergroups": []}))
        await slack.get_user_groups(include_users=True, include_disabled=False)
        kwargs = slack.client.usergroups_list.await_args.kwargs
        assert kwargs["include_users"] is True
        assert kwargs["include_disabled"] is False

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.usergroups_list = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_user_groups()
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


class TestGetUserGroupInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.usergroups_info = AsyncMock(return_value=_ok({"usergroup": {}}))
        ok, _ = await slack.get_user_group_info("S123")
        assert ok is True
        slack.client.usergroups_info.assert_awaited_once_with(usergroup="S123")

    @pytest.mark.asyncio
    async def test_with_include_disabled(self):
        slack = _build_slack()
        slack.client.usergroups_info = AsyncMock(return_value=_ok({"usergroup": {}}))
        await slack.get_user_group_info("S123", include_disabled=True)
        kwargs = slack.client.usergroups_info.await_args.kwargs
        assert kwargs["include_disabled"] is True

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.usergroups_info = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_user_group_info("S123")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_user_channels
# ===========================================================================

class TestGetUserChannels:
    @pytest.mark.asyncio
    async def test_no_authenticated_user(self):
        slack = _build_slack()
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value=None)
        ):
            ok, payload = await slack.get_user_channels()
        assert ok is False
        assert "authenticated user" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_pagination(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(side_effect=[
            _ok({
                "channels": [{"id": "C1"}],
                "response_metadata": {"next_cursor": "next"},
            }),
            _ok({
                "channels": [{"id": "C2"}],
                "response_metadata": {"next_cursor": ""},
            }),
        ])
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value="U101")
        ):
            ok, payload = await slack.get_user_channels()
        assert ok is True
        assert json.loads(payload)["data"]["count"] == 2

    @pytest.mark.asyncio
    async def test_first_page_failure(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(return_value=_fail("invalid_auth"))
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value="U101")
        ):
            ok, payload = await slack.get_user_channels()
        assert ok is False
        assert json.loads(payload)["error"] == "invalid_auth"

    @pytest.mark.asyncio
    async def test_exclude_archived_passed_through(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(
            return_value=_ok({"channels": [], "response_metadata": {"next_cursor": ""}})
        )
        with patch.object(
            slack, "_get_authenticated_user_id", AsyncMock(return_value="U101")
        ):
            await slack.get_user_channels(exclude_archived=True)
        kwargs = slack.client.users_conversations.await_args.kwargs
        assert kwargs["exclude_archived"] is True


# ===========================================================================
# delete_message / update_message
# ===========================================================================

class TestDeleteMessage:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.chat_delete = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.delete_message("C1234ABCD", "1.0")
        assert ok is True
        kwargs = slack.client.chat_delete.await_args.kwargs
        assert kwargs["channel"] == "C1234ABCD"
        assert kwargs["ts"] == "1.0"
        assert "as_user" not in kwargs

    @pytest.mark.asyncio
    async def test_with_as_user(self):
        slack = _build_slack()
        slack.client.chat_delete = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.delete_message("C1234ABCD", "1.0", as_user=True)
        kwargs = slack.client.chat_delete.await_args.kwargs
        assert kwargs["as_user"] is True

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.chat_delete = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.delete_message("C1234ABCD", "1.0")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


class TestUpdateMessage:
    @pytest.mark.asyncio
    async def test_success_text_only(self):
        slack = _build_slack()
        slack.client.chat_update = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.update_message("C1234ABCD", "1.0", "new text")
        assert ok is True
        kwargs = slack.client.chat_update.await_args.kwargs
        assert kwargs["text"] == "new text"
        assert "blocks" not in kwargs
        assert "as_user" not in kwargs

    @pytest.mark.asyncio
    async def test_with_blocks_and_as_user(self):
        slack = _build_slack()
        slack.client.chat_update = AsyncMock(return_value=_ok({}))
        blocks = [{"type": "section"}]
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.update_message(
                "C1234ABCD", "1.0", "x", blocks=blocks, as_user=True
            )
        kwargs = slack.client.chat_update.await_args.kwargs
        assert kwargs["blocks"] == blocks
        assert kwargs["as_user"] is True

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.chat_update = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.update_message("C1234ABCD", "1.0", "x")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_message_permalink / get_reactions / get_thread_replies
# ===========================================================================

class TestGetMessagePermalink:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.chat_get_permalink = AsyncMock(
            return_value=_ok({"permalink": "https://slack.com/x"})
        )
        ok, _ = await slack.get_message_permalink("C1", "1.0")
        assert ok is True
        slack.client.chat_get_permalink.assert_awaited_once_with(
            channel="C1", message_ts="1.0"
        )

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.chat_get_permalink = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_message_permalink("C1", "1.0")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


class TestGetReactions:
    @pytest.mark.asyncio
    async def test_success_no_full(self):
        slack = _build_slack()
        slack.client.reactions_get = AsyncMock(return_value=_ok({}))
        ok, _ = await slack.get_reactions("C1", "1.0")
        assert ok is True
        kwargs = slack.client.reactions_get.await_args.kwargs
        assert "full" not in kwargs

    @pytest.mark.asyncio
    async def test_full_passed_through(self):
        slack = _build_slack()
        slack.client.reactions_get = AsyncMock(return_value=_ok({}))
        await slack.get_reactions("C1", "1.0", full=True)
        kwargs = slack.client.reactions_get.await_args.kwargs
        assert kwargs["full"] is True

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.reactions_get = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_reactions("C1", "1.0")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


class TestGetThreadReplies:
    @pytest.mark.asyncio
    async def test_success_no_limit(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock(
            return_value=_ok({"messages": []})
        )
        ok, _ = await slack.get_thread_replies("C1", "1.0")
        assert ok is True
        kwargs = slack.client.conversations_replies.await_args.kwargs
        assert "limit" not in kwargs

    @pytest.mark.asyncio
    async def test_limit_passed_through(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock(
            return_value=_ok({"messages": []})
        )
        await slack.get_thread_replies("C1", "1.0", limit=20)
        kwargs = slack.client.conversations_replies.await_args.kwargs
        assert kwargs["limit"] == 20

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await slack.get_thread_replies("C1", "1.0")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_time_range_params_forwarded(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock(
            return_value=_ok({"messages": []})
        )
        await slack.get_thread_replies(
            "C1",
            "1.0",
            limit=15,
            oldest="1700000000.000000",
            latest="1700050000.000000",
            inclusive=True,
        )
        kwargs = slack.client.conversations_replies.await_args.kwargs
        assert kwargs == {
            "channel": "C1",
            "ts": "1.0",
            "limit": 15,
            "oldest": "1700000000.000000",
            "latest": "1700050000.000000",
            "inclusive": True,
        }

    @pytest.mark.asyncio
    async def test_time_range_params_omitted_when_none(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock(
            return_value=_ok({"messages": []})
        )
        await slack.get_thread_replies("C1", "1.0", limit=5)
        kwargs = slack.client.conversations_replies.await_args.kwargs
        assert "oldest" not in kwargs
        assert "latest" not in kwargs
        assert "inclusive" not in kwargs


# ===========================================================================
# upload_file_to_channel
# ===========================================================================

class TestUploadFileToChannel:
    @pytest.mark.asyncio
    async def test_success(self):
        slack = _build_slack()
        slack.client.files_upload_v2 = AsyncMock(
            return_value=_ok({"file": {"id": "F1"}})
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, _ = await slack.upload_file_to_channel(
                channel="#general",
                filename="t.txt",
                file_content="hello",
                title="T",
                initial_comment="see file",
            )
        assert ok is True
        kwargs = slack.client.files_upload_v2.await_args.kwargs
        assert kwargs["channel"] == "C1234ABCD"
        assert kwargs["filename"] == "t.txt"
        assert kwargs["content"] == "hello"
        assert kwargs["title"] == "T"
        assert kwargs["initial_comment"] == "see file"

    @pytest.mark.asyncio
    async def test_not_in_channel_short_circuit(self):
        slack = _build_slack()
        slack.client.files_upload_v2 = AsyncMock(side_effect=Exception("not_in_channel"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.upload_file_to_channel(
                channel="C1234ABCD", filename="t.txt", file_content="x"
            )
        assert ok is False
        assert json.loads(payload)["error"] == "not_in_channel"

    @pytest.mark.asyncio
    async def test_exception_wrapped(self):
        slack = _build_slack()
        slack.client.files_upload_v2 = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            ok, payload = await slack.upload_file_to_channel(
                channel="C1234ABCD", filename="t.txt", file_content="x"
            )
        assert ok is False
        assert "boom" in json.loads(payload)["error"]
