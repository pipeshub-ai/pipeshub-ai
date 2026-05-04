"""Unit tests for `app.agents.actions.slack.slack`.

Covers:
* Pydantic input schemas (validation, defaults, aliases)
* Internal helpers: `_handle_slack_response`, `_handle_slack_error`,
  `_convert_markdown_to_slack_mrkdwn`, `_get_authenticated_user_id`,
  `_resolve_channel`, `_resolve_user_identifier`, `_resolve_user_handle`
* All decorated tool methods (success, failure, exception paths)
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.slack.config import SlackResponse
from app.agents.actions.slack.slack import (
    SLACK_MENTION_RE,
    USER_ID_PREFIXES,
    USER_LOOKUP_CONCURRENCY,
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
    # Match `Slack.__init__`: the resolution helpers expect these caches and
    # the shared lookup semaphore to exist on the instance.
    slack._user_cache = {}
    slack._channel_cache = {}
    slack._lookup_sem = asyncio.Semaphore(USER_LOOKUP_CONCURRENCY)
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
        # ISO 8601 dates are converted to Slack epoch-string format before
        # being forwarded to conversations_history.
        oldest_iso = "2023-11-14T22:13:20Z"   # 1700000000
        latest_iso = "2023-11-15T12:06:40Z"   # 1700050000
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.get_channel_history(
                "C1234ABCD",
                limit=5,
                oldest=oldest_iso,
                latest=latest_iso,
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
            # ISO 8601 datetime gets converted to int epoch seconds.
            ok, _ = await slack.schedule_message(
                "#general", "**hi**", "2009-02-13T23:31:30Z"  # 1234567890
            )
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
        # `not-a-number` fails ISO 8601 parsing → SlackDateError surfaced.
        assert "Invalid date" in json.loads(payload)["error"]


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
        # ISO 8601 dates get converted to Slack epoch-string format.
        with patch.object(slack, "_resolve_channel", AsyncMock(return_value="C1")):
            await slack.get_thread_replies(
                "C1",
                "1.0",
                limit=15,
                oldest="2023-11-14T22:13:20Z",  # 1700000000
                latest="2023-11-15T12:06:40Z",  # 1700050000
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
        with patch.object(slack, "_resolve_channel", AsyncMock(return_value="C1")):
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


# ===========================================================================
# Module-level helpers introduced for ID-resolution / date enrichment
# ===========================================================================

from app.agents.actions.slack.slack import (  # noqa: E402
    SlackDateError,
    _add_iso_date_sibling,
    _is_user_id,
    _iso_to_slack_post_at,
    _iso_to_slack_ts,
    _slack_ts_to_iso,
)


class TestIsUserId:
    def test_u_prefix_accepted(self):
        assert _is_user_id("U123ABC456") is True

    def test_w_prefix_accepted_for_enterprise_grid(self):
        # Per https://docs.slack.dev/reference/methods/reactions.get
        assert _is_user_id("W0ABCDE123") is True

    def test_b_prefix_rejected_bot_ids_must_not_hit_users_info(self):
        assert _is_user_id("B01ABCD23") is False

    def test_channel_prefixes_rejected(self):
        for cid in ("C1234ABCD", "G1234ABCD", "D1234ABCD"):
            assert _is_user_id(cid) is False

    def test_short_string_rejected(self):
        assert _is_user_id("U123") is False

    def test_non_string_returns_false(self):
        for value in (None, 123, 12.5, ["U123ABC456"], {"id": "U123ABC456"}):
            assert _is_user_id(value) is False

    def test_lowercase_prefix_rejected(self):
        assert _is_user_id("u123ABC456") is False

    def test_user_id_prefixes_constant(self):
        assert USER_ID_PREFIXES == ('U', 'W')


class TestSlackMentionRe:
    def test_matches_u_and_w_prefix(self):
        text = "ping <@U0ABC1234> and <@W0XYZ9876>"
        assert SLACK_MENTION_RE.findall(text) == ["U0ABC1234", "W0XYZ9876"]

    def test_does_not_match_bot_or_channel_ids(self):
        text = "<@B01> <@C123> <@D9>"
        assert SLACK_MENTION_RE.findall(text) == []


class TestSlackDateError:
    def test_subclass_of_value_error(self):
        # SlackDateError extends ValueError so callers can catch broadly.
        assert issubclass(SlackDateError, ValueError)


class TestIsoToSlackTs:
    def test_none_returns_none(self):
        assert _iso_to_slack_ts(None) is None

    def test_empty_string_returns_none(self):
        assert _iso_to_slack_ts("") is None
        assert _iso_to_slack_ts("   ") is None

    def test_date_only_treated_as_midnight_utc(self):
        # 2023-11-14 00:00:00 UTC == epoch 1699920000
        assert _iso_to_slack_ts("2023-11-14") == "1699920000.000000"

    def test_naive_datetime_treated_as_utc(self):
        # 2023-11-14T22:13:20 UTC == 1700000000
        assert _iso_to_slack_ts("2023-11-14T22:13:20") == "1700000000.000000"

    def test_uppercase_z_suffix_normalized(self):
        assert _iso_to_slack_ts("2023-11-14T22:13:20Z") == "1700000000.000000"

    def test_lowercase_z_suffix_normalized(self):
        assert _iso_to_slack_ts("2023-11-14T22:13:20z") == "1700000000.000000"

    def test_explicit_offset_respected(self):
        # 22:13:20 +05:30 == 16:43:20 UTC; 2023-11-14T16:43:20Z == 1699980200
        result = _iso_to_slack_ts("2023-11-14T22:13:20+05:30")
        assert result == "1699980200.000000"

    def test_invalid_input_raises_slack_date_error(self):
        with pytest.raises(SlackDateError) as exc_info:
            _iso_to_slack_ts("not-a-date")
        assert "Invalid date 'not-a-date'" in str(exc_info.value)

    def test_raw_epoch_string_rejected(self):
        # Must NOT silently treat raw epoch as valid — that would mask a
        # caller bug given the new ISO contract.
        with pytest.raises(SlackDateError):
            _iso_to_slack_ts("1700000000.000000")

    def test_strips_surrounding_whitespace(self):
        assert _iso_to_slack_ts("  2023-11-14  ") == "1699920000.000000"


class TestIsoToSlackPostAt:
    def test_none_returns_none(self):
        assert _iso_to_slack_post_at(None) is None

    def test_empty_returns_none(self):
        assert _iso_to_slack_post_at("") is None

    def test_returns_int_seconds_no_fractional(self):
        # Slack's chat.scheduleMessage rejects fractional seconds.
        result = _iso_to_slack_post_at("2023-11-14T22:13:20Z")
        assert result == 1700000000
        assert isinstance(result, int)

    def test_invalid_propagates_slack_date_error(self):
        with pytest.raises(SlackDateError):
            _iso_to_slack_post_at("not-a-date")


class TestSlackTsToIso:
    def test_none_returns_none(self):
        assert _slack_ts_to_iso(None) is None

    def test_string_epoch_converted(self):
        assert _slack_ts_to_iso("1700000000.000000") == "2023-11-14T22:13:20Z"

    def test_int_epoch_converted(self):
        assert _slack_ts_to_iso(1700000000) == "2023-11-14T22:13:20Z"

    def test_float_epoch_converted(self):
        assert _slack_ts_to_iso(1700000000.0) == "2023-11-14T22:13:20Z"

    def test_zero_returns_none(self):
        # Slack uses 0 as a sentinel for "no value" (e.g. last_read on
        # never-read channels). Treat as missing rather than 1970-01-01.
        assert _slack_ts_to_iso(0) is None

    def test_negative_returns_none(self):
        assert _slack_ts_to_iso(-1) is None

    def test_non_numeric_string_returns_none(self):
        assert _slack_ts_to_iso("abc") is None

    def test_non_numeric_object_returns_none(self):
        assert _slack_ts_to_iso(object()) is None

    def test_overflow_returns_none(self):
        # Year 50000 — out of range for fromtimestamp on most platforms.
        assert _slack_ts_to_iso(10**15) is None


class TestAddIsoDateSibling:
    def test_missing_field_is_noop(self):
        d = {"a": 1}
        _add_iso_date_sibling(d, "ts")
        assert d == {"a": 1}

    def test_unparseable_value_is_noop(self):
        d = {"ts": "not-a-number"}
        _add_iso_date_sibling(d, "ts")
        assert "ts_date" not in d

    def test_zero_value_is_noop(self):
        d = {"last_read": "0"}
        _add_iso_date_sibling(d, "last_read")
        assert "last_read_date" not in d

    def test_valid_epoch_adds_sibling(self):
        d = {"ts": "1700000000.000000"}
        _add_iso_date_sibling(d, "ts")
        assert d["ts"] == "1700000000.000000"  # original preserved
        assert d["ts_date"] == "2023-11-14T22:13:20Z"

    def test_existing_sibling_not_overwritten(self):
        # Idempotent: re-running enrichment must not clobber a manually-set
        # sibling, otherwise repeated calls would lose precision.
        d = {"ts": "1700000000.000000", "ts_date": "manual"}
        _add_iso_date_sibling(d, "ts")
        assert d["ts_date"] == "manual"


# ===========================================================================
# _resolve_user_ids — bulk user-ID resolution with cache + fallback
# ===========================================================================

class TestResolveUserIds:
    @pytest.mark.asyncio
    async def test_empty_set_short_circuits_no_api_call(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        result = await slack._resolve_user_ids(set())
        assert result == {}
        slack.client.users_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_populates_display_name_real_name_email(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {
                "id": "U1",
                "name": "alice",
                "real_name": "Alice One",
                "profile": {"display_name": "alice_d", "email": "a@x.com"},
            }
        }))
        result = await slack._resolve_user_ids({"U123ABC456"})
        assert result == {
            "U123ABC456": {
                "display_name": "alice_d",
                "real_name": "Alice One",
                "email": "a@x.com",
            }
        }

    @pytest.mark.asyncio
    async def test_display_name_falls_back_through_chain(self):
        slack = _build_slack()
        # No display_name in profile, no real_name → fallback to user.name
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "bob", "profile": {}}
        }))
        result = await slack._resolve_user_ids({"U123ABC456"})
        assert result["U123ABC456"]["display_name"] == "bob"

    @pytest.mark.asyncio
    async def test_display_name_falls_back_to_id_when_all_missing(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"profile": {}}
        }))
        result = await slack._resolve_user_ids({"U123ABC456"})
        assert result["U123ABC456"]["display_name"] == "U123ABC456"

    @pytest.mark.asyncio
    async def test_failure_falls_back_to_id(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(side_effect=RuntimeError("net"))
        result = await slack._resolve_user_ids({"U123ABC456"})
        # Still returns an entry so callers don't need a None-check.
        assert result == {
            "U123ABC456": {"display_name": "U123ABC456", "real_name": None, "email": None}
        }

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self):
        slack = _build_slack()
        slack._user_cache["U123ABC456"] = {
            "display_name": "cached", "real_name": None, "email": None,
        }
        slack.client.users_info = AsyncMock()
        result = await slack._resolve_user_ids({"U123ABC456"})
        slack.client.users_info.assert_not_awaited()
        assert result["U123ABC456"]["display_name"] == "cached"

    @pytest.mark.asyncio
    async def test_only_misses_fetched(self):
        slack = _build_slack()
        slack._user_cache["U1AAAAAAA"] = {
            "display_name": "cached", "real_name": None, "email": None,
        }
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U2", "name": "fetched", "profile": {}}
        }))
        result = await slack._resolve_user_ids({"U1AAAAAAA", "U2BBBBBBB"})
        # Only the cache miss triggered a fetch.
        assert slack.client.users_info.await_count == 1
        slack.client.users_info.assert_awaited_with(user="U2BBBBBBB")
        assert result["U1AAAAAAA"]["display_name"] == "cached"
        assert result["U2BBBBBBB"]["display_name"] == "fetched"

    @pytest.mark.asyncio
    async def test_failure_does_not_poison_cache(self):
        # Negative results must NOT be cached — otherwise a transient
        # users_info failure would permanently surface the raw ID as the
        # display name. The next call must get a fresh chance to resolve.
        slack = _build_slack()
        slack.client.users_info = AsyncMock(side_effect=[
            RuntimeError("transient"),
            _ok({"user": {"id": "U1", "name": "alice",
                          "profile": {"display_name": "alice"}}}),
        ])
        first = await slack._resolve_user_ids({"U123ABC456"})
        assert first["U123ABC456"]["display_name"] == "U123ABC456"
        # Cache should still be empty — the failure was not memoised.
        assert "U123ABC456" not in slack._user_cache

        second = await slack._resolve_user_ids({"U123ABC456"})
        assert second["U123ABC456"]["display_name"] == "alice"
        assert slack.client.users_info.await_count == 2

    @pytest.mark.asyncio
    async def test_success_populates_cache_for_subsequent_calls(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        await slack._resolve_user_ids({"U123ABC456"})
        # Second call hits the cache, not the API.
        await slack._resolve_user_ids({"U123ABC456"})
        assert slack.client.users_info.await_count == 1
        assert slack._user_cache["U123ABC456"]["display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_uses_instance_lookup_semaphore(self):
        # The semaphore must come from the instance attribute so a parallel
        # _enrich_messages gather doesn't double the burst by spinning up
        # one semaphore per call site.
        slack = _build_slack()
        # Replace with a 1-permit semaphore to make contention observable.
        slack._lookup_sem = asyncio.Semaphore(1)
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        result = await slack._resolve_user_ids({"U1AAAAAAA", "U2BBBBBBB"})
        # Both resolved through the shared semaphore.
        assert slack.client.users_info.await_count == 2
        assert {k for k in result} == {"U1AAAAAAA", "U2BBBBBBB"}


# ===========================================================================
# _resolve_channel_ids — bulk channel-ID resolution
# ===========================================================================

class TestResolveChannelIds:
    @pytest.mark.asyncio
    async def test_empty_set_short_circuits(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock()
        result = await slack._resolve_channel_ids(set())
        assert result == {}
        slack.client.conversations_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_named_channel_resolved(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "C1", "name": "general"}
        }))
        result = await slack._resolve_channel_ids({"C1234ABCD"})
        assert result == {"C1234ABCD": "general"}

    @pytest.mark.asyncio
    async def test_im_without_name_uses_dm_partner_label(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "D1", "is_im": True, "user": "U9"}
        }))
        result = await slack._resolve_channel_ids({"D1234ABCD"})
        assert result == {"D1234ABCD": "DM:U9"}

    @pytest.mark.asyncio
    async def test_im_without_name_or_partner_falls_back_to_id(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "D1", "is_im": True}
        }))
        result = await slack._resolve_channel_ids({"D1234ABCD"})
        assert result == {"D1234ABCD": "D1234ABCD"}

    @pytest.mark.asyncio
    async def test_failure_falls_back_to_id(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(side_effect=RuntimeError("nope"))
        result = await slack._resolve_channel_ids({"C1234ABCD"})
        assert result == {"C1234ABCD": "C1234ABCD"}

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self):
        slack = _build_slack()
        slack._channel_cache["C1234ABCD"] = "cached-name"
        slack.client.conversations_info = AsyncMock()
        result = await slack._resolve_channel_ids({"C1234ABCD"})
        slack.client.conversations_info.assert_not_awaited()
        assert result["C1234ABCD"] == "cached-name"

    @pytest.mark.asyncio
    async def test_failure_does_not_poison_cache(self):
        # Same retry semantics as _resolve_user_ids — a failed
        # conversations_info must not bake the raw ID into the cache.
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(side_effect=[
            RuntimeError("transient"),
            _ok({"channel": {"id": "C1", "name": "general"}}),
        ])
        first = await slack._resolve_channel_ids({"C1234ABCD"})
        assert first["C1234ABCD"] == "C1234ABCD"
        assert "C1234ABCD" not in slack._channel_cache

        second = await slack._resolve_channel_ids({"C1234ABCD"})
        assert second["C1234ABCD"] == "general"
        assert slack.client.conversations_info.await_count == 2

    @pytest.mark.asyncio
    async def test_success_populates_cache_for_subsequent_calls(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "C1", "name": "general"}
        }))
        await slack._resolve_channel_ids({"C1234ABCD"})
        await slack._resolve_channel_ids({"C1234ABCD"})
        assert slack.client.conversations_info.await_count == 1
        assert slack._channel_cache["C1234ABCD"] == "general"

    @pytest.mark.asyncio
    async def test_shares_lookup_sem_with_user_resolver(self):
        # _resolve_user_ids and _resolve_channel_ids must drain the same
        # semaphore so the effective concurrency cap holds even when both
        # resolvers run concurrently inside one _enrich_messages pass.
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "C1", "name": "general"}
        }))
        # Sentinel: both resolvers must use this exact instance, not a
        # newly-allocated one per call.
        sentinel = asyncio.Semaphore(USER_LOOKUP_CONCURRENCY)
        slack._lookup_sem = sentinel
        await asyncio.gather(
            slack._resolve_user_ids({"U123ABC456"}),
            slack._resolve_channel_ids({"C1234ABCD"}),
        )
        assert slack._lookup_sem is sentinel


# ===========================================================================
# _collect_user_ids_from_message
# ===========================================================================

class TestCollectUserIdsFromMessage:
    def test_non_dict_no_op(self):
        ids: set = set()
        slack = _build_slack()
        slack._collect_user_ids_from_message("not a dict", ids)
        slack._collect_user_ids_from_message(None, ids)
        assert ids == set()

    def test_collects_text_mentions(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {"text": "hi <@U123ABC456> and <@W999XYZ12>"}, ids
        )
        assert ids == {"U123ABC456", "W999XYZ12"}

    def test_collects_user_and_parent_user_id(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {"user": "U123ABC456", "parent_user_id": "U999XYZ12"}, ids
        )
        assert ids == {"U123ABC456", "U999XYZ12"}

    def test_skips_bot_user_field(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message({"user": "B01BOT123"}, ids)
        assert ids == set()

    def test_collects_replies_user(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {"replies": [{"user": "U1AAAAAAA"}, {"user": "U2BBBBBBB"}]}, ids
        )
        assert ids == {"U1AAAAAAA", "U2BBBBBBB"}

    def test_collects_reply_users_roster(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {"reply_users": ["U1AAAAAAA", "B01BOT123", "U2BBBBBBB"]}, ids
        )
        # Bot ID filtered out by _is_user_id.
        assert ids == {"U1AAAAAAA", "U2BBBBBBB"}

    def test_collects_reactions_users(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {"reactions": [{"name": "tada", "users": ["U1AAAAAAA", "U2BBBBBBB"]}]},
            ids,
        )
        assert ids == {"U1AAAAAAA", "U2BBBBBBB"}

    def test_collects_files_user(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {"files": [{"user": "U1AAAAAAA"}, {"user": "U2BBBBBBB"}]}, ids
        )
        assert ids == {"U1AAAAAAA", "U2BBBBBBB"}

    def test_collects_edited_user(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {"edited": {"user": "U1AAAAAAA", "ts": "1.0"}}, ids
        )
        assert ids == {"U1AAAAAAA"}

    def test_collects_room_fields(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_user_ids_from_message(
            {
                "subtype": "huddle_thread",
                "room": {
                    "created_by": "U1AAAAAAA",
                    "participant_history": ["U2BBBBBBB", "U3CCCCCCC"],
                },
            },
            ids,
        )
        assert ids == {"U1AAAAAAA", "U2BBBBBBB", "U3CCCCCCC"}

    def test_handles_none_lists_gracefully(self):
        slack = _build_slack()
        ids: set = set()
        # Slack often returns None instead of [] for absent collections.
        slack._collect_user_ids_from_message(
            {"replies": None, "reply_users": None, "reactions": None,
             "files": None, "edited": None, "room": None},
            ids,
        )
        assert ids == set()


# ===========================================================================
# _collect_channel_ids_from_message
# ===========================================================================

class TestCollectChannelIdsFromMessage:
    def test_non_dict_no_op(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_channel_ids_from_message("nope", ids)
        assert ids == set()

    def test_collects_top_level_channel(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_channel_ids_from_message({"channel": "C1234ABCD"}, ids)
        assert ids == {"C1234ABCD"}

    def test_skips_user_id_in_channel_field(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_channel_ids_from_message({"channel": "U123ABC456"}, ids)
        # User-ID prefix → not a channel.
        assert ids == set()

    def test_collects_room_channels(self):
        slack = _build_slack()
        ids: set = set()
        slack._collect_channel_ids_from_message(
            {"room": {"channels": ["C1AAAAAAA", "G2BBBBBBB", "D3CCCCCCC", "X4DDDDDDD"]}},
            ids,
        )
        # X-prefix is not a channel — filtered out.
        assert ids == {"C1AAAAAAA", "G2BBBBBBB", "D3CCCCCCC"}


# ===========================================================================
# _apply_resolution_to_message
# ===========================================================================

class TestApplyResolutionToMessage:
    def test_non_dict_returned_as_is(self):
        slack = _build_slack()
        assert slack._apply_resolution_to_message("not-a-msg", {}, {}) == "not-a-msg"
        assert slack._apply_resolution_to_message(None, {}, {}) is None

    def test_resolves_text_mentions_and_emits_meta(self):
        slack = _build_slack()
        msg = {"text": "hi <@U123ABC456>"}
        out = slack._apply_resolution_to_message(
            msg,
            {"U123ABC456": "alice"},
            {"U123ABC456": "a@x.com"},
        )
        assert out["resolved_text"] == "hi @alice"
        assert out["mentions"] == [
            {"id": "U123ABC456", "display_name": "alice", "email": "a@x.com"}
        ]

    def test_unresolved_mention_kept_as_id_in_resolved_text(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"text": "hi <@U123ABC456>"}, {}, {}
        )
        assert out["resolved_text"] == "hi @U123ABC456"
        assert out["mentions"] == [
            {"id": "U123ABC456", "display_name": None, "email": None}
        ]

    def test_no_mentions_no_meta_field(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message({"text": "hi"}, {}, {})
        assert "mentions" not in out

    def test_author_email_only_added_when_mapping_exists(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"user": "U123ABC456"},
            {"U123ABC456": "alice"},
            {},  # no email mapping
        )
        assert out["user_display_name"] == "alice"
        assert "user_email" not in out

    def test_parent_user_display_name(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"parent_user_id": "U123ABC456"},
            {"U123ABC456": "alice"},
            {},
        )
        assert out["parent_user_display_name"] == "alice"

    def test_replies_get_user_display_name_and_ts_date(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"replies": [{"user": "U123ABC456", "ts": "1700000000.000000"}]},
            {"U123ABC456": "alice"},
            {},
        )
        rep = out["replies"][0]
        assert rep["user_display_name"] == "alice"
        assert rep["ts_date"] == "2023-11-14T22:13:20Z"

    def test_reply_users_display_names(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"reply_users": ["U1AAAAAAA", "U2BBBBBBB"]},
            {"U1AAAAAAA": "alice"},  # only one resolved
            {},
        )
        # Unresolved IDs fall through unchanged.
        assert out["reply_users_display_names"] == ["alice", "U2BBBBBBB"]

    def test_channel_name_added_from_lookup(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"channel": "C1234ABCD"},
            {},
            {},
            channel_id_to_name={"C1234ABCD": "general"},
        )
        assert out["channel_name"] == "general"

    def test_reactions_users_display_names(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"reactions": [
                {"name": "tada", "users": ["U1AAAAAAA", "U2BBBBBBB"]}
            ]},
            {"U1AAAAAAA": "alice", "U2BBBBBBB": "bob"},
            {},
        )
        assert out["reactions"][0]["user_display_names"] == ["alice", "bob"]

    def test_files_user_display_name_and_iso_dates(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"files": [{"user": "U1", "created": 1700000000, "timestamp": 1700000000}]},
            {},
            {},
        )
        # `_apply_resolution_to_message` doesn't filter via `_is_user_id` —
        # it just looks up. With an empty mapping, the file user stays raw
        # (no `user_display_name` injected) and only the ISO date siblings
        # are added.
        assert out["files"][0].get("user_display_name") is None
        assert out["files"][0]["created_date"] == "2023-11-14T22:13:20Z"
        assert out["files"][0]["timestamp_date"] == "2023-11-14T22:13:20Z"

    def test_files_full_resolution(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"files": [{
                "user": "U1AAAAAAA",
                "created": 1700000000,
                "timestamp": "1700000000.000000",
                "updated": 1700000000,
            }]},
            {"U1AAAAAAA": "alice"},
            {"U1AAAAAAA": "a@x.com"},
        )
        f = out["files"][0]
        assert f["user_display_name"] == "alice"
        assert f["user_email"] == "a@x.com"
        assert f["created_date"] == "2023-11-14T22:13:20Z"
        assert f["timestamp_date"] == "2023-11-14T22:13:20Z"
        assert f["updated_date"] == "2023-11-14T22:13:20Z"

    def test_edited_user_display_name_and_ts_date(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"edited": {"user": "U1AAAAAAA", "ts": "1700000000.000000"}},
            {"U1AAAAAAA": "alice"},
            {},
        )
        assert out["edited"]["user_display_name"] == "alice"
        assert out["edited"]["ts_date"] == "2023-11-14T22:13:20Z"

    def test_room_resolution(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {
                "subtype": "huddle_thread",
                "room": {
                    "created_by": "U1AAAAAAA",
                    "participant_history": ["U1AAAAAAA", "U2BBBBBBB"],
                    "channels": ["C1AAAAAAA"],
                    "date_start": 1700000000,
                },
            },
            {"U1AAAAAAA": "alice", "U2BBBBBBB": "bob"},
            {},
            channel_id_to_name={"C1AAAAAAA": "general"},
        )
        room = out["room"]
        assert room["created_by_display_name"] == "alice"
        assert room["participant_history_display_names"] == ["alice", "bob"]
        assert room["channel_names"] == ["general"]
        assert room["date_start_date"] == "2023-11-14T22:13:20Z"

    def test_top_level_epoch_fields_get_iso_siblings(self):
        slack = _build_slack()
        out = slack._apply_resolution_to_message(
            {"ts": "1700000000.000000", "thread_ts": "1700000000.000000"},
            {},
            {},
        )
        assert out["ts_date"] == "2023-11-14T22:13:20Z"
        assert out["thread_ts_date"] == "2023-11-14T22:13:20Z"

    def test_input_message_not_mutated(self):
        slack = _build_slack()
        msg = {"text": "<@U1AAAAAAA>", "user": "U1AAAAAAA"}
        slack._apply_resolution_to_message(
            msg, {"U1AAAAAAA": "alice"}, {}
        )
        # Returned a shallow copy; original is untouched.
        assert "resolved_text" not in msg
        assert "user_display_name" not in msg


# ===========================================================================
# _enrich_reactable — shared helper extracted in commit a7cc01ff
# ===========================================================================

class TestEnrichReactable:
    @pytest.mark.asyncio
    async def test_no_user_ids_returns_input_unchanged(self):
        # Bare-text comment with no user fields → return identity (not even
        # a copy). The caller relies on this to skip writing back.
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        obj = {"comment": "a string", "reactions": []}
        out = await slack._enrich_reactable(obj)
        assert out is obj
        slack.client.users_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_top_level_user_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        out = await slack._enrich_reactable({"user": "U1AAAAAAA"})
        assert out["user_display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_reaction_user_lists_resolved(self):
        slack = _build_slack()
        # Dispatch by `user=` kwarg — `_resolve_user_ids` iterates a set, so
        # call order is not the insertion order; a positional side_effect list
        # would bind responses to the wrong IDs.
        responses = {
            "U1AAAAAAA": _ok({"user": {"id": "U1", "name": "alice",
                                       "profile": {"display_name": "alice"}}}),
            "U2BBBBBBB": _ok({"user": {"id": "U2", "name": "bob",
                                       "profile": {"display_name": "bob"}}}),
        }
        slack.client.users_info = AsyncMock(
            side_effect=lambda *, user, **_: responses[user]
        )
        out = await slack._enrich_reactable({
            "reactions": [
                {"name": "tada", "users": ["U1AAAAAAA", "U2BBBBBBB"]}
            ]
        })
        assert out["reactions"][0]["user_display_names"] == ["alice", "bob"]

    @pytest.mark.asyncio
    async def test_unresolvable_users_kept_as_id_in_display_names(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(side_effect=RuntimeError("net"))
        # One ID is U-prefix (will be looked up but fail); the other is a
        # bot ID that _is_user_id rejects and should be passed through raw.
        out = await slack._enrich_reactable({
            "user": "U1AAAAAAA",
            "reactions": [
                {"name": "tada", "users": ["U1AAAAAAA", "B01BOT123"]}
            ],
        })
        # `_resolve_user_ids` falls back to `{display_name: uid}` on failure
        # so callers always get a usable label — the raw ID surfaces as the
        # display name for both the top-level user and the reaction list.
        assert out["user_display_name"] == "U1AAAAAAA"
        assert out["reactions"][0]["user_display_names"] == [
            "U1AAAAAAA", "B01BOT123",
        ]

    @pytest.mark.asyncio
    async def test_input_not_mutated(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        original = {
            "user": "U1AAAAAAA",
            "reactions": [{"name": "tada", "users": ["U1AAAAAAA"]}],
        }
        out = await slack._enrich_reactable(original)
        # Returned object is a copy with the enrichment fields.
        assert out is not original
        assert "user_display_name" not in original
        assert "user_display_names" not in original["reactions"][0]
        assert out["user_display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_non_dict_reaction_entries_passed_through(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        out = await slack._enrich_reactable({
            "user": "U1AAAAAAA",
            "reactions": ["weird", {"name": "tada", "users": ["U1AAAAAAA"]}],
        })
        assert out["reactions"][0] == "weird"
        assert out["reactions"][1]["user_display_names"] == ["alice"]


# ===========================================================================
# _enrich_messages
# ===========================================================================

class TestEnrichMessages:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        slack = _build_slack()
        assert await slack._enrich_messages([]) == []
        assert await slack._enrich_messages(None) == []

    @pytest.mark.asyncio
    async def test_resolves_user_and_channel_ids_in_one_pass(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice", "profile": {"display_name": "alice"}}
        }))
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "C1", "name": "general"}
        }))
        out = await slack._enrich_messages([
            {"user": "U1AAAAAAA", "channel": "C1AAAAAAA", "text": "hello"}
        ])
        assert out[0]["user_display_name"] == "alice"
        assert out[0]["channel_name"] == "general"


# ===========================================================================
# _resolve_user_id_list
# ===========================================================================

class TestResolveUserIdList:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        slack = _build_slack()
        assert await slack._resolve_user_id_list([]) == []
        assert await slack._resolve_user_id_list(None) == []

    @pytest.mark.asyncio
    async def test_preserves_order_and_skips_non_strings(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(side_effect=[
            _ok({"user": {"id": "U1", "name": "alice",
                          "profile": {"display_name": "alice", "email": "a@x.com"}}}),
            _ok({"user": {"id": "U2", "name": "bob",
                          "profile": {"display_name": "bob"}}}),
        ])
        out = await slack._resolve_user_id_list(
            ["U1AAAAAAA", 42, None, "U2BBBBBBB"]  # type: ignore[list-item]
        )
        ids = [entry["id"] for entry in out]
        assert ids == ["U1AAAAAAA", "U2BBBBBBB"]
        # Every entry has display_name (falls back to id for non-resolvable).
        assert all(entry["display_name"] for entry in out)

    @pytest.mark.asyncio
    async def test_unresolvable_id_falls_back_to_id_as_display(self):
        slack = _build_slack()
        # Non-user-id string is kept in the output but not resolved.
        out = await slack._resolve_user_id_list(["bot-name"])
        assert out == [
            {"id": "bot-name", "display_name": "bot-name",
             "real_name": None, "email": None}
        ]


# ===========================================================================
# _enrich_pin_items
# ===========================================================================

class TestEnrichPinItems:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        slack = _build_slack()
        assert await slack._enrich_pin_items([]) == []
        assert await slack._enrich_pin_items(None) == []

    @pytest.mark.asyncio
    async def test_message_pin_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        items = [{
            "type": "message",
            "created": 1700000000,
            "created_by": "U1AAAAAAA",
            "message": {"user": "U1AAAAAAA", "text": "hi"},
        }]
        out = await slack._enrich_pin_items(items)
        item = out[0]
        assert item["created_by_display_name"] == "alice"
        assert item["created_date"] == "2023-11-14T22:13:20Z"
        assert item["message"]["user_display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_file_pin_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice", "email": "a@x.com"}}
        }))
        items = [{
            "type": "file",
            "file": {"user": "U1AAAAAAA", "created": 1700000000},
        }]
        out = await slack._enrich_pin_items(items)
        f = out[0]["file"]
        assert f["user_display_name"] == "alice"
        assert f["user_email"] == "a@x.com"
        assert f["created_date"] == "2023-11-14T22:13:20Z"

    @pytest.mark.asyncio
    async def test_file_comment_pin_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        items = [{
            "type": "file_comment",
            "comment": {"user": "U1AAAAAAA", "created": 1700000000},
        }]
        out = await slack._enrich_pin_items(items)
        c = out[0]["comment"]
        assert c["user_display_name"] == "alice"
        assert c["created_date"] == "2023-11-14T22:13:20Z"

    @pytest.mark.asyncio
    async def test_non_dict_items_passed_through(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        items = ["not a dict", {"type": "message", "message": {"text": "hi"}}]
        out = await slack._enrich_pin_items(items)
        assert out[0] == "not a dict"
        assert isinstance(out[1], dict)


# ===========================================================================
# _enrich_conversations
# ===========================================================================

class TestEnrichConversations:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        slack = _build_slack()
        assert await slack._enrich_conversations([]) == []
        assert await slack._enrich_conversations(None) == []

    @pytest.mark.asyncio
    async def test_im_partner_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U9", "name": "alice",
                     "profile": {"display_name": "alice", "email": "a@x.com"}}
        }))
        out = await slack._enrich_conversations([
            {"id": "D1", "is_im": True, "user": "U9AAAAAAA", "created": 1700000000}
        ])
        c = out[0]
        assert c["user_display_name"] == "alice"
        assert c["user_email"] == "a@x.com"
        assert c["created_date"] == "2023-11-14T22:13:20Z"

    @pytest.mark.asyncio
    async def test_channel_creator_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        out = await slack._enrich_conversations([
            {"id": "C1", "name": "general", "creator": "U1AAAAAAA",
             "latest": {"ts": "1700000000.000000", "text": "hi"}}
        ])
        c = out[0]
        assert c["creator_display_name"] == "alice"
        # `latest` is a sub-message preview — gets ts_date.
        assert c["latest"]["ts_date"] == "2023-11-14T22:13:20Z"

    @pytest.mark.asyncio
    async def test_no_user_ids_still_runs_date_enrichment(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        out = await slack._enrich_conversations([
            {"id": "C1", "name": "general", "created": 1700000000}
        ])
        # No user-ids, so users.info is never called.
        slack.client.users_info.assert_not_awaited()
        assert out[0]["created_date"] == "2023-11-14T22:13:20Z"

    @pytest.mark.asyncio
    async def test_non_dict_passed_through(self):
        slack = _build_slack()
        out = await slack._enrich_conversations(["weird"])
        assert out == ["weird"]

    @pytest.mark.asyncio
    async def test_unread_count_display_is_not_treated_as_epoch(self):
        # `unread_count_display` is a count of unread messages, not an epoch.
        # Treating it as one (as we used to) yielded nonsense `…_date`
        # siblings like 1970-01-01T00:00:42Z, which the LLM then misread as
        # the channel's last activity. Commit a7cc01ff dropped it from
        # _EPOCH_FIELDS_ON_CONVERSATION.
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        out = await slack._enrich_conversations([
            {"id": "C1", "name": "general",
             "created": 1700000000,
             "unread_count_display": 42,
             "last_read": "1700000000.000000"},
        ])
        c = out[0]
        # Real epoch fields still get ISO siblings.
        assert c["created_date"] == "2023-11-14T22:13:20Z"
        assert c["last_read_date"] == "2023-11-14T22:13:20Z"
        # The count must NOT get a `_date` sibling.
        assert "unread_count_display_date" not in c
        # Original count is preserved.
        assert c["unread_count_display"] == 42


# ===========================================================================
# _enrich_usergroups
# ===========================================================================

class TestEnrichUsergroups:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        slack = _build_slack()
        assert await slack._enrich_usergroups([]) == []
        assert await slack._enrich_usergroups(None) == []

    @pytest.mark.asyncio
    async def test_no_user_ids_passes_through_unchanged(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        groups = [{"id": "S1", "name": "engineering"}]
        out = await slack._enrich_usergroups(groups)
        slack.client.users_info.assert_not_awaited()
        assert out == groups

    @pytest.mark.asyncio
    async def test_resolves_by_fields_and_users(self):
        slack = _build_slack()
        # Dispatch by `user=` kwarg — set-based fan-out doesn't preserve
        # insertion order, so a positional side_effect list is order-fragile.
        responses = {
            "U1AAAAAAA": _ok({"user": {"id": "U1", "name": "alice",
                                       "profile": {"display_name": "alice",
                                                   "email": "a@x.com"}}}),
            "U2BBBBBBB": _ok({"user": {"id": "U2", "name": "bob",
                                       "profile": {"display_name": "bob"}}}),
        }
        slack.client.users_info = AsyncMock(
            side_effect=lambda *, user, **_: responses[user]
        )
        groups = [{
            "id": "S1",
            "created_by": "U1AAAAAAA",
            "updated_by": "U1AAAAAAA",
            "users": ["U1AAAAAAA", "U2BBBBBBB"],
        }]
        out = await slack._enrich_usergroups(groups)
        g = out[0]
        assert g["created_by_display_name"] == "alice"
        assert g["updated_by_display_name"] == "alice"
        # `resolved_users` is added alongside the original `users` list.
        assert g["users"] == ["U1AAAAAAA", "U2BBBBBBB"]
        assert {u["id"] for u in g["resolved_users"]} == {"U1AAAAAAA", "U2BBBBBBB"}

    @pytest.mark.asyncio
    async def test_deleted_by_field_resolved(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        out = await slack._enrich_usergroups([{"deleted_by": "U1AAAAAAA"}])
        assert out[0]["deleted_by_display_name"] == "alice"


# ===========================================================================
# _enrich_search_response
# ===========================================================================

class TestEnrichSearchResponse:
    @pytest.mark.asyncio
    async def test_non_dict_returned_as_is(self):
        slack = _build_slack()
        assert await slack._enrich_search_response("not-a-dict") == "not-a-dict"  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_messages_matches_enriched(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        slack.client.conversations_info = AsyncMock()
        out = await slack._enrich_search_response({
            "messages": {"matches": [{"user": "U1AAAAAAA", "text": "hi"}]}
        })
        assert out["messages"]["matches"][0]["user_display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_files_matches_enriched(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice", "email": "a@x.com"}}
        }))
        out = await slack._enrich_search_response({
            "files": {"matches": [{"user": "U1AAAAAAA", "name": "log.txt"}]}
        })
        f = out["files"]["matches"][0]
        assert f["user_display_name"] == "alice"
        assert f["user_email"] == "a@x.com"

    @pytest.mark.asyncio
    async def test_files_section_with_no_user_ids_skips_resolve(self):
        slack = _build_slack()
        slack.client.users_info = AsyncMock()
        out = await slack._enrich_search_response({
            "files": {"matches": [{"name": "log.txt"}]}
        })
        slack.client.users_info.assert_not_awaited()
        assert out["files"]["matches"] == [{"name": "log.txt"}]


# ===========================================================================
# Tool-method changes: ISO date conversion for get_channel_history
# ===========================================================================

class TestGetChannelHistoryDateConversion:
    @pytest.mark.asyncio
    async def test_iso_date_converted_to_slack_ts(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock(
            return_value=_ok({"messages": []})
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            await slack.get_channel_history("C1", oldest="2023-11-14")
        kwargs = slack.client.conversations_history.await_args.kwargs
        assert kwargs["oldest"] == "1699920000.000000"
        assert kwargs["latest"] is None

    @pytest.mark.asyncio
    async def test_invalid_oldest_returns_slack_date_error(self):
        slack = _build_slack()
        slack.client.conversations_history = AsyncMock()
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_channel_history(
                "C1", oldest="garbage"
            )
        assert ok is False
        assert "Invalid date 'garbage'" in json.loads(payload)["error"]
        # Must not call the API after a date-parse failure.
        slack.client.conversations_history.assert_not_awaited()


# ===========================================================================
# Tool-method changes: get_thread_replies — channel resolution + date conversion
# ===========================================================================

class TestGetThreadRepliesEnrichment:
    @pytest.mark.asyncio
    async def test_resolves_channel_name_to_id(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock(
            return_value=_ok({"messages": []})
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.get_thread_replies("#bugs", "1.0")
        kwargs = slack.client.conversations_replies.await_args.kwargs
        assert kwargs["channel"] == "C1234ABCD"

    @pytest.mark.asyncio
    async def test_invalid_oldest_returns_error_no_api_call(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock()
        ok, payload = await slack.get_thread_replies(
            "C1", "1.0", oldest="garbage"
        )
        assert ok is False
        assert "Invalid date" in json.loads(payload)["error"]
        slack.client.conversations_replies.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_messages_get_enriched_with_user_display_name(self):
        slack = _build_slack()
        slack.client.conversations_replies = AsyncMock(return_value=_ok({
            "messages": [{"user": "U1AAAAAAA", "text": "hi"}]
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_thread_replies("C1", "1.0")
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["messages"][0]["user_display_name"] == "alice"


# ===========================================================================
# Tool-method changes: schedule_message — ISO conversion + post_at_date sibling
# ===========================================================================

class TestScheduleMessageDateConversion:
    @pytest.mark.asyncio
    async def test_iso_with_offset_converted_to_int_epoch(self):
        slack = _build_slack()
        slack.client.chat_schedule_message = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            await slack.schedule_message(
                "C1", "hi", "2009-02-14T05:01:30+05:30"  # 1234567890 UTC
            )
        kwargs = slack.client.chat_schedule_message.await_args.kwargs
        assert kwargs["post_at"] == 1234567890
        assert isinstance(kwargs["post_at"], int)

    @pytest.mark.asyncio
    async def test_post_at_date_sibling_added_on_success(self):
        slack = _build_slack()
        slack.client.chat_schedule_message = AsyncMock(return_value=_ok({
            "channel": "C1", "scheduled_message_id": "Q1", "post_at": 1700000000
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.schedule_message(
                "C1", "hi", "2023-11-14T22:13:20Z"
            )
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["post_at"] == 1700000000
        assert body["data"]["post_at_date"] == "2023-11-14T22:13:20Z"

    @pytest.mark.asyncio
    async def test_empty_post_at_returns_required_error(self):
        slack = _build_slack()
        slack.client.chat_schedule_message = AsyncMock()
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.schedule_message("C1", "hi", "")
        assert ok is False
        assert json.loads(payload)["error"] == "post_at is required"
        slack.client.chat_schedule_message.assert_not_awaited()


# ===========================================================================
# Tool-method changes: get_unread_messages — channel resolution + enrichment
# ===========================================================================

class TestGetUnreadMessagesEnrichment:
    @pytest.mark.asyncio
    async def test_resolves_channel_name_before_calling_apis(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "C1", "name": "general", "created": 1700000000}
        }))
        slack.client.conversations_history = AsyncMock(return_value=_ok({
            "messages": []
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.get_unread_messages("#general")
        slack.client.conversations_info.assert_awaited_once_with(
            channel="C1234ABCD"
        )
        slack.client.conversations_history.assert_awaited_once_with(
            channel="C1234ABCD", limit=50
        )

    @pytest.mark.asyncio
    async def test_messages_and_channel_info_get_enriched(self):
        slack = _build_slack()
        slack.client.conversations_info = AsyncMock(return_value=_ok({
            "channel": {"id": "C1", "name": "general",
                        "creator": "U1AAAAAAA", "created": 1700000000}
        }))
        slack.client.conversations_history = AsyncMock(return_value=_ok({
            "messages": [{"user": "U1AAAAAAA", "text": "hi"}]
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_unread_messages("C1")
        assert ok is True
        body = json.loads(payload)
        info = body["data"]["channel_info"]["channel"]
        assert info["creator_display_name"] == "alice"
        assert info["created_date"] == "2023-11-14T22:13:20Z"
        assert body["data"]["recent_messages"][0]["user_display_name"] == "alice"


# ===========================================================================
# Tool-method changes: get_pinned_messages — channel resolution + enrichment
# ===========================================================================

class TestGetPinnedMessagesEnrichment:
    @pytest.mark.asyncio
    async def test_channel_name_resolved_before_pins_list(self):
        slack = _build_slack()
        slack.client.pins_list = AsyncMock(return_value=_ok({"items": []}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.get_pinned_messages("#general")
        slack.client.pins_list.assert_awaited_once_with(channel="C1234ABCD")

    @pytest.mark.asyncio
    async def test_pin_items_enriched(self):
        slack = _build_slack()
        slack.client.pins_list = AsyncMock(return_value=_ok({
            "items": [{
                "type": "message",
                "created": 1700000000,
                "created_by": "U1AAAAAAA",
                "message": {"user": "U1AAAAAAA", "text": "hi"},
            }]
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_pinned_messages("C1")
        assert ok is True
        item = json.loads(payload)["data"]["items"][0]
        assert item["created_by_display_name"] == "alice"
        assert item["message"]["user_display_name"] == "alice"


# ===========================================================================
# Tool-method changes: get_reactions — channel resolution + enrichment branches
# ===========================================================================

class TestGetReactionsEnrichment:
    @pytest.mark.asyncio
    async def test_channel_name_resolved(self):
        slack = _build_slack()
        slack.client.reactions_get = AsyncMock(return_value=_ok({}))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1234ABCD")
        ):
            await slack.get_reactions("#general", "1.0")
        kwargs = slack.client.reactions_get.await_args.kwargs
        assert kwargs["channel"] == "C1234ABCD"

    @pytest.mark.asyncio
    async def test_message_reaction_enriched(self):
        slack = _build_slack()
        slack.client.reactions_get = AsyncMock(return_value=_ok({
            "type": "message",
            "message": {"user": "U1AAAAAAA", "text": "hi"},
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_reactions("C1", "1.0")
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["message"]["user_display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_file_reaction_enriched(self):
        slack = _build_slack()
        slack.client.reactions_get = AsyncMock(return_value=_ok({
            "type": "file",
            "file": {
                "user": "U1AAAAAAA",
                "reactions": [
                    {"name": "tada", "users": ["U2BBBBBBB"]},
                ],
            },
        }))
        # Dispatch by `user=` kwarg — set-based fan-out doesn't preserve
        # insertion order, so a positional side_effect list is order-fragile.
        responses = {
            "U1AAAAAAA": _ok({"user": {"id": "U1", "name": "alice",
                                       "profile": {"display_name": "alice"}}}),
            "U2BBBBBBB": _ok({"user": {"id": "U2", "name": "bob",
                                       "profile": {"display_name": "bob"}}}),
        }
        slack.client.users_info = AsyncMock(
            side_effect=lambda *, user, **_: responses[user]
        )
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_reactions("C1", "1.0")
        assert ok is True
        f = json.loads(payload)["data"]["file"]
        assert f["user_display_name"] == "alice"
        assert f["reactions"][0]["user_display_names"] == ["bob"]

    @pytest.mark.asyncio
    async def test_file_comment_reaction_enriched(self):
        slack = _build_slack()
        slack.client.reactions_get = AsyncMock(return_value=_ok({
            "type": "file_comment",
            "file_comment": {
                "user": "U1AAAAAAA",
                "reactions": [{"name": "tada", "users": ["U1AAAAAAA"]}],
            },
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_reactions("C1", "1.0")
        assert ok is True
        c = json.loads(payload)["data"]["file_comment"]
        assert c["user_display_name"] == "alice"
        assert c["reactions"][0]["user_display_names"] == ["alice"]


# ===========================================================================
# Tool-method changes: search_messages — enrichment of matches
# ===========================================================================

class TestSearchMessagesEnrichment:
    @pytest.mark.asyncio
    async def test_messages_matches_enriched(self):
        slack = _build_slack()
        slack.client.search_messages = AsyncMock(return_value=_ok({
            "messages": {
                "matches": [{"user": "U1AAAAAAA", "text": "release notes"}]
            }
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        ok, payload = await slack.search_messages(query="release")
        assert ok is True
        match = json.loads(payload)["data"]["messages"]["matches"][0]
        assert match["user_display_name"] == "alice"


# ===========================================================================
# Tool-method changes: get_user_groups / get_user_group_info — enrichment
# ===========================================================================

class TestGetUserGroupsEnrichment:
    @pytest.mark.asyncio
    async def test_usergroups_enriched(self):
        slack = _build_slack()
        slack.client.usergroups_list = AsyncMock(return_value=_ok({
            "usergroups": [{
                "id": "S1",
                "name": "engineering",
                "created_by": "U1AAAAAAA",
                "users": ["U1AAAAAAA"],
            }]
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        ok, payload = await slack.get_user_groups()
        assert ok is True
        g = json.loads(payload)["data"]["usergroups"][0]
        assert g["created_by_display_name"] == "alice"
        assert g["resolved_users"][0]["display_name"] == "alice"


class TestGetUserGroupInfoEnrichment:
    @pytest.mark.asyncio
    async def test_singular_usergroup_enriched(self):
        slack = _build_slack()
        slack.client.usergroups_info = AsyncMock(return_value=_ok({
            "usergroup": {
                "id": "S1", "name": "eng",
                "created_by": "U1AAAAAAA",
                "users": ["U1AAAAAAA"],
            }
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        ok, payload = await slack.get_user_group_info(usergroup="S1")
        assert ok is True
        g = json.loads(payload)["data"]["usergroup"]
        assert g["created_by_display_name"] == "alice"

    @pytest.mark.asyncio
    async def test_plural_usergroups_list_enriched(self):
        # Defensive: some helpers return `usergroups: [...]` even on .info.
        slack = _build_slack()
        slack.client.usergroups_info = AsyncMock(return_value=_ok({
            "usergroups": [{
                "id": "S1",
                "created_by": "U1AAAAAAA",
                "users": [],
            }]
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        ok, payload = await slack.get_user_group_info(usergroup="S1")
        assert ok is True
        g = json.loads(payload)["data"]["usergroups"][0]
        assert g["created_by_display_name"] == "alice"


# ===========================================================================
# Tool-method changes: get_channel_members(_by_id) — resolved_members
# ===========================================================================

class TestGetChannelMembersEnrichment:
    @pytest.mark.asyncio
    async def test_resolved_members_added(self):
        slack = _build_slack()
        slack.client.conversations_members = AsyncMock(return_value=_ok({
            "members": ["U1AAAAAAA", "U2BBBBBBB"]
        }))
        slack.client.users_info = AsyncMock(side_effect=[
            _ok({"user": {"id": "U1", "name": "alice",
                          "profile": {"display_name": "alice", "email": "a@x.com"}}}),
            _ok({"user": {"id": "U2", "name": "bob",
                          "profile": {"display_name": "bob"}}}),
        ])
        with patch.object(
            slack, "_resolve_channel", AsyncMock(return_value="C1")
        ):
            ok, payload = await slack.get_channel_members("C1")
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["members"] == ["U1AAAAAAA", "U2BBBBBBB"]
        ids = [m["id"] for m in body["data"]["resolved_members"]]
        assert ids == ["U1AAAAAAA", "U2BBBBBBB"]


class TestGetChannelMembersByIdEnrichment:
    @pytest.mark.asyncio
    async def test_resolved_members_added(self):
        slack = _build_slack()
        slack.client.conversations_members = AsyncMock(return_value=_ok({
            "members": ["U1AAAAAAA"]
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        ok, payload = await slack.get_channel_members_by_id("C1")
        assert ok is True
        body = json.loads(payload)
        assert body["data"]["resolved_members"][0]["display_name"] == "alice"


# ===========================================================================
# Tool-method changes: fetch_channels / get_user_channels / get_user_conversations
# ===========================================================================

class TestFetchChannelsEnrichment:
    @pytest.mark.asyncio
    async def test_im_partner_pre_resolved(self):
        slack = _build_slack()
        slack.client.conversations_list = AsyncMock(return_value=_ok({
            "channels": [
                {"id": "D1", "is_im": True, "user": "U9AAAAAAA",
                 "created": 1700000000},
            ],
            "response_metadata": {"next_cursor": ""},
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U9", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        ok, payload = await slack.fetch_channels()
        assert ok is True
        chan = json.loads(payload)["data"]["channels"][0]
        assert chan["user_display_name"] == "alice"
        assert chan["created_date"] == "2023-11-14T22:13:20Z"


class TestGetUserChannelsEnrichment:
    @pytest.mark.asyncio
    async def test_creator_resolved(self):
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(return_value=_ok({
            "channels": [{"id": "C1", "name": "g", "creator": "U1AAAAAAA"}],
            "response_metadata": {"next_cursor": ""},
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        with patch.object(
            slack, "_get_authenticated_user_id",
            AsyncMock(return_value="U1AAAAAAA"),
        ):
            ok, payload = await slack.get_user_channels()
        assert ok is True
        c = json.loads(payload)["data"]["channels"][0]
        assert c["creator_display_name"] == "alice"


class TestGetUserConversationsLimitPathEnrichment:
    @pytest.mark.asyncio
    async def test_limit_path_enriches_channels(self):
        # When `limit` is provided, the method short-circuits the pagination
        # loop and goes through the dedicated single-call branch.
        slack = _build_slack()
        slack.client.users_conversations = AsyncMock(return_value=_ok({
            "channels": [{"id": "C1", "name": "g", "creator": "U1AAAAAAA"}],
        }))
        slack.client.users_info = AsyncMock(return_value=_ok({
            "user": {"id": "U1", "name": "alice",
                     "profile": {"display_name": "alice"}}
        }))
        with patch.object(
            slack, "_get_authenticated_user_id",
            AsyncMock(return_value="U1AAAAAAA"),
        ):
            ok, payload = await slack.get_user_conversations(limit=5)
        assert ok is True
        c = json.loads(payload)["data"]["channels"][0]
        assert c["creator_display_name"] == "alice"


# ===========================================================================
# Tool-method changes: get_scheduled_messages — date sibling enrichment
# ===========================================================================

class TestGetScheduledMessagesDateEnrichment:
    @pytest.mark.asyncio
    async def test_post_at_and_date_created_get_iso_siblings(self):
        slack = _build_slack()
        slack.client.chat_scheduled_messages_list = AsyncMock(return_value=_ok({
            "scheduled_messages": [{
                "id": "Q1",
                "post_at": 1700000000,
                "date_created": 1700000000,
            }]
        }))
        ok, payload = await slack.get_scheduled_messages()
        assert ok is True
        item = json.loads(payload)["data"]["scheduled_messages"][0]
        assert item["post_at"] == 1700000000
        assert item["post_at_date"] == "2023-11-14T22:13:20Z"
        assert item["date_created_date"] == "2023-11-14T22:13:20Z"

    @pytest.mark.asyncio
    async def test_non_dict_items_passed_through(self):
        slack = _build_slack()
        slack.client.chat_scheduled_messages_list = AsyncMock(return_value=_ok({
            "scheduled_messages": ["weird"]
        }))
        ok, payload = await slack.get_scheduled_messages()
        assert ok is True
        assert json.loads(payload)["data"]["scheduled_messages"] == ["weird"]
