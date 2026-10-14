"""Behaviour tests for the Slack agent tools.

Each test drives a tool the way the agent does and checks what Slack would
receive and what the agent is told back. See ``slack_tool_fakes`` for
what is real and what is faked.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest
from slack_tool_fakes import (
    ME,
    USER_TOKEN,
    FakeSlackApi,
    build_slack_tool,
    rate_limited,
    result,
    slack_error,
    user,
)

if TYPE_CHECKING:
    from app.agents.actions.slack.slack import Slack

GENERAL = "C0GENERAL1"
RANDOM = "C0RANDOM01"
DM = "D0DMCHAN01"
ANN = user("U0ANNAAAAA", "Ann", "ann@example.com")
JOANNA = user("U0JOANNA00", "Joanna Park", "joanna@example.com")
SAM = user("U0SAMAAAAA", "Sam", "sam@example.com")
SAMANTHA = user("U0SAMANTHA", "Samantha Lee", "samantha@example.com")


@pytest.fixture
def api() -> FakeSlackApi:
    return FakeSlackApi()


@pytest.fixture
def slack(api: FakeSlackApi) -> Slack:
    return build_slack_tool(api)


def failure(outcome: tuple[bool, str]) -> dict[str, Any]:
    """A failed call, checked to be safe to show: no token, no raw SDK dump."""
    ok, payload = outcome
    assert ok is False, payload
    for leaked in (USER_TOKEN, "Bearer", "The server responded with", "slack.com/api"):
        assert leaked not in payload, f"{leaked!r} leaked into: {payload}"
    data = json.loads(payload)
    assert data["success"] is False
    return data


def explanation(data: dict[str, Any]) -> str:
    """What the agent will relay: the human message, else the error text."""
    return data.get("message") or data["error"]


def members_page(members: list[dict], next_cursor: str = "") -> dict[str, Any]:
    return {"members": members, "response_metadata": {"next_cursor": next_cursor}}


# ---------------------------------------------------------------------------
# Sending to channels
# ---------------------------------------------------------------------------


class TestSendMessage:
    async def test_posts_markdown_as_mrkdwn_with_the_users_token(self, slack, api) -> None:
        api.on("chat.postMessage", {"channel": GENERAL, "ts": "1700000000.000100"})

        ok, data = result(await slack.send_message(GENERAL, "**Release** is out, see [notes](https://x.test)"))

        assert ok is True
        call = api.called("chat.postMessage")[0]
        assert call.args == {"channel": GENERAL, "text": "*Release* is out, see <https://x.test|notes>", "mrkdwn": True}
        assert call.headers["authorization"] == f"Bearer {USER_TOKEN}"
        assert data["data"]["ts"] == "1700000000.000100"

    async def test_channel_name_is_resolved_across_conversation_pages(self, slack, api) -> None:
        api.on("conversations.list",
               {"channels": [{"id": RANDOM, "name": "random"}], "response_metadata": {"next_cursor": "c2"}},
               {"channels": [{"id": GENERAL, "name": "general"}], "response_metadata": {"next_cursor": ""}})
        api.on("chat.postMessage", {"channel": GENERAL, "ts": "1.1"})

        ok, _ = result(await slack.send_message("#general", "hi"))

        assert ok is True
        pages = api.called("conversations.list")
        assert [p.args.get("cursor") for p in pages] == [None, "c2"]
        assert api.called("chat.postMessage")[0].args["channel"] == GENERAL

    async def test_rate_limit_says_how_long_to_wait(self, slack, api) -> None:
        api.on("chat.postMessage", rate_limited(retry_after=12))

        data = failure(await slack.send_message(GENERAL, "hi"))

        assert data["error"] == "ratelimited"
        assert data["retry_after"] == "12"
        message = explanation(data)
        assert "12 seconds" in message and "try again" in message.lower()

    @pytest.mark.parametrize("code", ["invalid_auth", "token_revoked", "not_authed"])
    async def test_rejected_sign_in_asks_the_user_to_reconnect(self, slack, api, code) -> None:
        api.on("chat.postMessage", slack_error(code))

        data = failure(await slack.send_message(GENERAL, "hi"))

        assert data["error"] == code
        assert "Reconnect the Slack toolset" in explanation(data)

    async def test_missing_permission_asks_to_reconnect_and_approve(self, slack, api) -> None:
        api.on("chat.postMessage", slack_error("missing_scope", needed="chat:write"))

        data = failure(await slack.send_message(GENERAL, "hi"))

        message = explanation(data)
        assert "permission" in message and "Reconnect the Slack toolset" in message
        assert "xoxb" not in message and "bot" not in message.lower()

    async def test_not_in_channel_speaks_to_the_user_not_a_bot(self, slack, api) -> None:
        api.on("chat.postMessage", slack_error("not_in_channel"))

        data = failure(await slack.send_message(GENERAL, "hi"))

        assert data["error"] == "not_in_channel"
        message = explanation(data)
        assert "not a member" in message and "bot" not in message.lower()

    async def test_unknown_channel_points_to_fetch_channels(self, slack, api) -> None:
        api.on("chat.postMessage", slack_error("channel_not_found"))

        data = failure(await slack.send_message(GENERAL, "hi"))

        assert data["error"] == "channel_not_found"
        assert "fetch_channels" in explanation(data)

    async def test_uncommon_error_code_is_explained_without_the_sdk_dump(self, slack, api) -> None:
        api.on("chat.postMessage", slack_error("is_archived"))

        data = failure(await slack.send_message(GENERAL, "hi"))

        assert data["error"] == "is_archived"
        assert "archived" in explanation(data)

    async def test_slack_outage_is_reported_as_temporary(self, slack, api) -> None:
        api.on("chat.postMessage", slack_error("fatal_error", status=500))

        data = failure(await slack.send_message(GENERAL, "hi"))

        assert "try again" in explanation(data).lower()


class TestSendToMultipleChannels:
    async def test_each_channel_gets_the_message_and_failures_are_per_channel(self, slack, api) -> None:
        api.on("chat.postMessage", lambda args: {"channel": args["channel"], "ts": "1.1"} if args["channel"] == GENERAL else slack_error("channel_not_found"))

        ok, data = result(await slack.send_message_to_multiple_channels([GENERAL, RANDOM], "hi"))

        assert ok is False
        results = {r["channel"]: r for r in data["data"]["results"]}
        assert results[GENERAL]["success"] is True
        assert results[RANDOM]["success"] is False
        assert results[RANDOM]["error"] == "channel_not_found"

    async def test_empty_channel_list_is_refused_not_reported_as_sent(self, slack, api) -> None:
        data = failure(await slack.send_message_to_multiple_channels([], "hi"))

        assert "channel" in data["error"].lower()
        assert api.calls == []

    async def test_json_string_channel_list_is_read_as_a_list(self, slack, api) -> None:
        api.on("chat.postMessage", lambda args: {"channel": args["channel"], "ts": "1.1"})

        ok, _ = result(await slack.send_message_to_multiple_channels(f'["{GENERAL}", "{RANDOM}"]', "hi"))  # type: ignore[arg-type]

        assert ok is True
        assert [c.args["channel"] for c in api.called("chat.postMessage")] == [GENERAL, RANDOM]


class TestReplyAndSchedule:
    async def test_reply_to_latest_message_threads_under_it(self, slack, api) -> None:
        api.on("conversations.history", {"messages": [{"ts": "1700000000.000200", "text": "q?"}]})
        api.on("chat.postMessage", {"ts": "1700000001.000000"})

        ok, _ = result(await slack.reply_to_message(GENERAL, "answer", latest_message=True))

        assert ok is True
        assert api.called("conversations.history")[0].args["limit"] == "1"
        assert api.called("chat.postMessage")[0].args["thread_ts"] == "1700000000.000200"

    async def test_reply_without_a_thread_is_refused(self, slack, api) -> None:
        data = failure(await slack.reply_to_message(GENERAL, "answer"))

        assert "thread" in data["error"].lower()
        assert api.called("chat.postMessage") == []

    async def test_schedule_converts_the_time_to_epoch_seconds(self, slack, api) -> None:
        api.on("chat.scheduleMessage", {"scheduled_message_id": "Q1", "post_at": 1790762400})

        ok, data = result(await slack.schedule_message(GENERAL, "standup", "2026-09-30T10:00:00Z"))

        assert ok is True
        assert api.called("chat.scheduleMessage")[0].args["post_at"] == 1790762400
        assert data["data"]["post_at_date"] == "2026-09-30T10:00:00Z"

    async def test_schedule_rejects_an_unreadable_time_before_calling_slack(self, slack, api) -> None:
        data = failure(await slack.schedule_message(GENERAL, "standup", "tomorrow morning"))

        assert "tomorrow morning" in data["error"]
        assert api.calls == []

    async def test_schedule_in_the_past_is_explained(self, slack, api) -> None:
        api.on("chat.scheduleMessage", slack_error("time_in_past"))

        data = failure(await slack.schedule_message(GENERAL, "standup", "2020-01-01T00:00:00Z"))

        assert "future" in explanation(data)


# ---------------------------------------------------------------------------
# Reading channels
# ---------------------------------------------------------------------------


class TestChannelHistory:
    async def test_time_window_and_limit_reach_slack_and_authors_get_names(self, slack, api) -> None:
        api.on("conversations.history", {"messages": [{"ts": "1777000000.000100", "user": ANN["id"], "text": "hi <@U0SAMAAAAA>"}], "has_more": False})
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})

        ok, data = result(await slack.get_channel_history(GENERAL, limit=20, oldest="2026-04-30", latest="2026-05-01T00:00:00Z"))

        assert ok is True
        args = api.called("conversations.history")[0].args
        assert args["limit"] == "20"
        assert args["oldest"] == "1777507200.000000"
        assert args["latest"] == "1777593600.000000"
        message = data["data"]["messages"][0]
        assert message["user_display_name"] == "Ann"
        assert "@Sam" in message["resolved_text"]

    async def test_unreadable_date_is_refused_before_calling_slack(self, slack, api) -> None:
        data = failure(await slack.get_channel_history(GENERAL, oldest="last week"))

        assert "last week" in data["error"]
        assert api.calls == []

    async def test_history_failure_is_reported(self, slack, api) -> None:
        api.on("conversations.history", slack_error("not_in_channel"))

        data = failure(await slack.get_channel_history(GENERAL))

        assert data["error"] == "not_in_channel"

    async def test_channel_info(self, slack, api) -> None:
        api.on("conversations.info", {"channel": {"id": GENERAL, "name": "general", "creator": ANN["id"]}})
        api.on("users.info", {"user": ANN})

        ok, data = result(await slack.get_channel_info(GENERAL))

        assert ok is True
        assert data["data"]["channel"]["name"] == "general"


# ---------------------------------------------------------------------------
# Direct messages and who gets them
# ---------------------------------------------------------------------------


class TestDirectMessages:
    async def test_email_is_looked_up_and_the_dm_channel_opened(self, slack, api) -> None:
        api.on("users.lookupByEmail", {"user": ANN})
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"channel": DM, "ts": "1.1"})

        ok, _ = result(await slack.send_direct_message("ann@example.com", "hello"))

        assert ok is True
        assert api.called("users.lookupByEmail")[0].args["email"] == "ann@example.com"
        assert api.called("conversations.open")[0].args["users"] == ANN["id"]
        assert api.called("chat.postMessage")[0].args["channel"] == DM

    async def test_exact_name_is_messaged(self, slack, api) -> None:
        api.on("users.list", members_page([SAMANTHA, SAM]))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        ok, _ = result(await slack.send_direct_message("Sam", "hello"))

        assert ok is True
        assert api.called("conversations.open")[0].args["users"] == SAM["id"]

    async def test_ambiguous_name_sends_nothing(self, slack, api) -> None:
        api.on("users.list", members_page([SAMANTHA, user("U0SAMPATEL", "Samuel Patel")]))

        data = failure(await slack.send_direct_message("Sam", "hello"))

        assert "Multiple users" in data["error"]
        assert api.called("chat.postMessage") == []

    async def test_email_nobody_has_is_not_matched_to_someone_by_name(self, slack, api) -> None:
        # sam@partner.test is not in the workspace; the message must not go to "Sam".
        api.on("users.lookupByEmail", slack_error("users_not_found"))
        api.on("users.list", members_page([SAM, ANN]))

        data = failure(await slack.send_direct_message("sam@partner.test", "contract attached"))

        assert "not found" in data["error"]
        assert api.called("conversations.open") == []
        assert api.called("chat.postMessage") == []

    async def test_enterprise_grid_user_id_is_messaged_directly(self, slack, api) -> None:
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        ok, _ = result(await slack.send_direct_message("W0ENTGRID1", "hello"))

        assert ok is True
        assert api.called("users.list") == []
        assert api.called("conversations.open")[0].args["users"] == "W0ENTGRID1"

    async def test_longer_name_is_not_matched_to_a_shorter_directory_name(self, slack, api) -> None:
        # "Joanna" contains "ann"; that must not make Ann the recipient.
        api.on("users.list", members_page([ANN]))

        data = failure(await slack.send_direct_message("Joanna", "your review"))

        assert "not found" in data["error"]
        assert api.called("chat.postMessage") == []

    async def test_directory_failure_is_not_reported_as_unknown_user(self, slack, api) -> None:
        api.on("users.list", rate_limited(retry_after=20))

        data = failure(await slack.send_direct_message("Joanna Park", "hello"))

        message = explanation(data)
        assert "not found" not in data["error"]
        assert "20 seconds" in message
        assert api.called("chat.postMessage") == []

    async def test_email_lookup_failure_is_not_reported_as_unknown_user(self, slack, api) -> None:
        api.on("users.lookupByEmail", slack_error("missing_scope"))

        data = failure(await slack.get_dm_history("ann@example.com"))

        assert data["error"] == "missing_scope"
        assert "Reconnect the Slack toolset" in explanation(data)
        assert api.called("conversations.open") == []

    @pytest.mark.xfail(strict=True, reason=(
        "A single partial name match ('Sam' -> 'Samantha Lee') is messaged without asking. "
        "The Teams tool asks for confirmation instead; changing Slack to match is a product call."
    ))
    async def test_single_partial_match_is_confirmed_before_messaging(self, slack, api) -> None:
        api.on("users.list", members_page([SAMANTHA, ANN]))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        await slack.send_direct_message("Sam", "your review is due")

        assert api.called("chat.postMessage") == []

    async def test_dm_history_reads_the_opened_dm_channel(self, slack, api) -> None:
        api.on("users.lookupByEmail", {"user": ANN})
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("conversations.history", {"messages": []})

        ok, _ = result(await slack.get_dm_history("ann@example.com", limit=5))

        assert ok is True
        assert api.called("conversations.history")[0].args == {"channel": DM, "limit": "5"}

    async def test_mentions_resolve_to_user_ids(self, slack, api) -> None:
        api.on("users.lookupByEmail", {"user": ANN})
        api.on("chat.postMessage", {"ts": "1.1"})

        ok, _ = result(await slack.send_message_with_mentions(GENERAL, "ping @ann@example.com", mentions=["ann@example.com"]))

        assert ok is True
        assert api.called("chat.postMessage")[0].args["text"] == f"ping <@{ANN['id']}>"


# ---------------------------------------------------------------------------
# Listing: pagination and limits
# ---------------------------------------------------------------------------


class TestListing:
    async def test_limit_larger_than_one_page_follows_the_cursor(self, slack, api) -> None:
        # Slack may return fewer members than asked for and hand back a cursor.
        api.on("users.list", members_page([ANN, SAM], "c2"), members_page([SAMANTHA], "c3"), members_page([JOANNA]))

        ok, data = result(await slack.get_users_list(limit=3))

        assert ok is True
        assert [m["id"] for m in data["data"]["members"]] == [ANN["id"], SAM["id"], SAMANTHA["id"]]
        assert [c.args.get("cursor") for c in api.called("users.list")] == [None, "c2"]

    async def test_without_limit_every_page_is_read(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "c2"), members_page([SAM]))

        ok, data = result(await slack.get_users_list())

        assert ok is True
        assert data["data"]["count"] == 2

    async def test_list_cut_short_by_a_failed_page_says_so(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "c2"), rate_limited())

        ok, data = result(await slack.get_users_list())

        assert ok is True
        assert data["data"]["count"] == 1
        assert data["data"]["complete"] is False
        assert "only part" in data["message"].lower()

    async def test_first_page_failure_is_a_failure(self, slack, api) -> None:
        api.on("users.list", slack_error("missing_scope"))

        failure(await slack.get_users_list())

    async def test_user_channels_are_listed_for_the_signed_in_user(self, slack, api) -> None:
        api.on("auth.test", {"user_id": ME})
        api.on("users.conversations", {"channels": [{"id": GENERAL, "name": "general"}], "response_metadata": {"next_cursor": ""}})

        ok, data = result(await slack.get_user_channels())

        assert ok is True
        assert api.called("users.conversations")[0].args["user"] == ME
        assert data["data"]["count"] == 1

    async def test_user_channels_cut_short_say_so(self, slack, api) -> None:
        api.on("auth.test", {"user_id": ME})
        api.on("users.conversations", {"channels": [{"id": GENERAL}], "response_metadata": {"next_cursor": "c2"}}, slack_error("ratelimited", status=429))

        ok, data = result(await slack.get_user_channels())

        assert ok is True
        assert data["data"]["complete"] is False

    async def test_user_conversations_with_limit_follow_the_cursor(self, slack, api) -> None:
        api.on("auth.test", {"user_id": ME})
        api.on("users.conversations",
               {"channels": [{"id": GENERAL}], "response_metadata": {"next_cursor": "c2"}},
               {"channels": [{"id": RANDOM}], "response_metadata": {"next_cursor": ""}})

        ok, data = result(await slack.get_user_conversations(limit=2))

        assert ok is True
        assert [c["id"] for c in data["data"]["channels"]] == [GENERAL, RANDOM]

    async def test_fetch_channels_cut_short_say_so(self, slack, api) -> None:
        api.on("conversations.list", {"channels": [{"id": GENERAL, "name": "general"}], "response_metadata": {"next_cursor": "c2"}}, slack_error("internal_error", status=500))

        ok, data = result(await slack.fetch_channels())

        assert ok is True
        assert data["data"]["count"] == 1
        assert data["data"]["complete"] is False

    async def test_signed_in_user_unknown_is_a_failure(self, slack, api) -> None:
        api.on("auth.test", slack_error("invalid_auth"))

        failure(await slack.get_user_channels())

        assert api.called("users.conversations") == []


# ---------------------------------------------------------------------------
# Status, search, message edits
# ---------------------------------------------------------------------------


class TestStatusSearchAndEdits:
    async def test_status_expiry_is_now_plus_duration(self, slack, api) -> None:
        api.on("users.profile.set", {"profile": {}})
        before = int(time.time())

        ok, _ = result(await slack.set_user_status("In a meeting", "calendar", duration_seconds=3600))

        assert ok is True
        args = api.called("users.profile.set")[0].args
        assert args["profile"] == {"status_text": "In a meeting", "status_emoji": ":calendar:"}
        assert before + 3600 <= args["status_expiration"] <= int(time.time()) + 3600

    async def test_clearing_status_blanks_text_and_emoji(self, slack, api) -> None:
        api.on("users.profile.set", {"profile": {}})

        await slack.set_user_status("", "")

        args = api.called("users.profile.set")[0].args
        assert args["profile"] == {"status_text": "", "status_emoji": ""}
        assert args["status_expiration"] == 0

    async def test_search_builds_modifiers_from_a_person_and_dates(self, slack, api) -> None:
        api.on("users.lookupByEmail", {"user": ANN})
        api.on("users.info", {"user": ANN})
        api.on("search.messages", {"messages": {"matches": []}})

        ok, _ = result(await slack.search_messages("launch", channel="#general", from_user="ann@example.com", after="2026-01-01", count=5))

        assert ok is True
        args = api.called("search.messages")[0].args
        assert args["query"] == "in:general after:2026-01-01 from:@ann launch"
        assert args["count"] == "5"

    async def test_editing_someone_elses_message_is_explained(self, slack, api) -> None:
        api.on("chat.update", slack_error("cant_update_message"))

        data = failure(await slack.update_message(GENERAL, "1.1", "edited"))

        assert "your own" in explanation(data)

    async def test_reacting_twice_is_explained(self, slack, api) -> None:
        api.on("reactions.add", slack_error("already_reacted"))

        data = failure(await slack.add_reaction(GENERAL, "1.1", "thumbsup"))

        assert "already" in explanation(data)


# ---------------------------------------------------------------------------
# Members, people search, groups, pins, reactions, threads
# ---------------------------------------------------------------------------


class TestPeopleAndChannelDetails:
    async def test_channel_members_follow_every_page_and_get_names(self, slack, api) -> None:
        api.on("conversations.members", {"members": [ANN["id"]], "response_metadata": {"next_cursor": "c2"}}, {"members": [SAM["id"]], "response_metadata": {"next_cursor": ""}})
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})

        ok, data = result(await slack.get_channel_members(GENERAL))

        assert ok is True
        assert data["data"]["members"] == [ANN["id"], SAM["id"]]
        assert {m["display_name"] for m in data["data"]["resolved_members"]} == {"Ann", "Sam"}
        assert [c.args.get("cursor") for c in api.called("conversations.members")] == [None, "c2"]

    async def test_channel_members_by_id_failure_is_reported(self, slack, api) -> None:
        api.on("conversations.members", slack_error("channel_not_found"))

        data = failure(await slack.get_channel_members_by_id(GENERAL))

        assert data["error"] == "channel_not_found"

    async def test_search_users_matches_names_across_pages(self, slack, api) -> None:
        api.on("users.list", members_page([ANN, user("U0BOTAAAAA", "Annbot", is_bot=True)], "c2"), members_page([JOANNA, user("U0GONEAAAA", "Anna Old", deleted=True)]))

        ok, data = result(await slack.search_users("ann"))

        assert ok is True
        assert [u["id"] for u in data["data"]["users"]] == [ANN["id"], JOANNA["id"]]

    async def test_search_users_failure_is_not_reported_as_no_match(self, slack, api) -> None:
        api.on("users.list", rate_limited(retry_after=5))

        data = failure(await slack.search_users("ann"))

        assert "5 seconds" in explanation(data)

    async def test_search_users_needs_two_characters(self, slack, api) -> None:
        failure(await slack.search_users("a"))

        assert api.calls == []

    async def test_user_info_by_email(self, slack, api) -> None:
        api.on("users.lookupByEmail", {"user": ANN})
        api.on("users.info", {"user": {**ANN, "tz": "Europe/Berlin"}})

        ok, data = result(await slack.get_user_info("ann@example.com"))

        assert ok is True
        assert api.called("users.info")[0].args["user"] == ANN["id"]
        assert "Ann" in json.dumps(data)

    async def test_user_groups_get_member_names(self, slack, api) -> None:
        api.on("usergroups.list", {"usergroups": [{"id": "S1", "name": "oncall", "users": [ANN["id"]], "created_by": SAM["id"]}]})
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})

        ok, data = result(await slack.get_user_groups(include_users=True))

        assert ok is True
        assert api.called("usergroups.list")[0].args["include_users"] in ("1", "true", True)
        assert "Ann" in json.dumps(data["data"]["usergroups"])

    async def test_user_groups_failure_is_reported(self, slack, api) -> None:
        api.on("usergroups.list", slack_error("missing_scope"))

        data = failure(await slack.get_user_groups())

        assert "Reconnect the Slack toolset" in explanation(data)

    async def test_user_group_info_finds_the_group_by_id(self, slack, api) -> None:
        # Slack has no usergroups.info method; the group comes from usergroups.list.
        api.on("usergroups.list", {"usergroups": [
            {"id": "S1", "handle": "oncall", "users": [ANN["id"]]},
            {"id": "S2", "handle": "design", "users": [SAM["id"]], "created_by": ANN["id"]},
        ]})
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})

        ok, data = result(await slack.get_user_group_info("S2", include_disabled=True))

        assert ok is True
        assert api.methods().count("usergroups.list") == 1
        args = api.called("usergroups.list")[0].args
        assert args["include_users"] in ("1", "true", True) and args["include_disabled"] in ("1", "true", True)
        assert data["data"]["usergroup"]["handle"] == "design"
        assert "Sam" in json.dumps(data["data"]["usergroup"])

    async def test_unknown_user_group_says_how_to_find_one(self, slack, api) -> None:
        api.on("usergroups.list", {"usergroups": [{"id": "S1", "handle": "oncall"}]})

        data = failure(await slack.get_user_group_info("S404"))

        assert "get_user_groups" in explanation(data)

    async def test_pinned_messages_and_failure(self, slack, api) -> None:
        api.on("pins.list", {"items": [{"type": "message", "created": 1777000000, "message": {"ts": "1.1", "user": ANN["id"], "text": "read me"}}]}, slack_error("channel_not_found"))
        api.on("users.info", {"user": ANN})

        ok, data = result(await slack.get_pinned_messages(GENERAL))
        second = failure(await slack.get_pinned_messages(GENERAL))

        assert ok is True
        assert data["data"]["items"][0]["message"]["user_display_name"] == "Ann"
        assert second["error"] == "channel_not_found"

    async def test_reactions_on_a_message_and_failure(self, slack, api) -> None:
        api.on("reactions.get", {"type": "message", "message": {"ts": "1.1", "user": ANN["id"], "reactions": [{"name": "eyes", "users": [SAM["id"]], "count": 1}]}}, slack_error("message_not_found"))
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})

        ok, data = result(await slack.get_reactions(GENERAL, "1.1", full=True))
        second = failure(await slack.get_reactions(GENERAL, "9.9"))

        assert ok is True
        assert "Sam" in json.dumps(data["data"]["message"]["reactions"])
        assert "could not find that message" in explanation(second)

    async def test_thread_replies_window_and_failure(self, slack, api) -> None:
        api.on("conversations.replies", {"messages": [{"ts": "1.1", "user": ANN["id"], "text": "q"}, {"ts": "1.2", "user": SAM["id"], "text": "a"}]}, slack_error("thread_not_found"))
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})

        ok, data = result(await slack.get_thread_replies(GENERAL, "1.1", limit=10, oldest="2026-04-30"))
        second = failure(await slack.get_thread_replies(GENERAL, "1.1"))

        assert ok is True
        args = api.called("conversations.replies")[0].args
        assert args["ts"] == "1.1" and args["limit"] == "10" and args["oldest"] == "1777507200.000000"
        assert [m["user_display_name"] for m in data["data"]["messages"]] == ["Ann", "Sam"]
        assert "thread" in explanation(second)

    async def test_scheduled_messages_get_readable_dates(self, slack, api) -> None:
        api.on("chat.scheduledMessages.list", {"scheduled_messages": [{"id": "Q1", "post_at": 1790762400, "date_created": 1790000000}]}, slack_error("invalid_channel"))

        ok, data = result(await slack.get_scheduled_messages(channel=GENERAL))
        failure(await slack.get_scheduled_messages())

        assert ok is True
        assert data["data"]["scheduled_messages"][0]["post_at_date"] == "2026-09-30T10:00:00Z"
        assert api.called("chat.scheduledMessages.list")[0].args["channel"] == GENERAL


class TestResolutionEdges:
    async def test_person_on_a_later_directory_page_is_found(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "c2"), members_page([JOANNA]))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        ok, _ = result(await slack.send_direct_message("Joanna Park", "hello"))

        assert ok is True
        assert api.called("conversations.open")[0].args["users"] == JOANNA["id"]

    async def test_names_that_only_contain_the_query_elsewhere_are_ambiguous(self, slack, api) -> None:
        api.on("users.list", members_page([user("U0LEEANNA0", "Lee Anna"), user("U0MARIANNA", "Marianna Ruiz")]))

        data = failure(await slack.send_direct_message("anna", "hello"))

        assert "Multiple users" in data["error"]
        assert api.called("chat.postMessage") == []

    async def test_deleted_and_bot_users_are_never_recipients(self, slack, api) -> None:
        api.on("users.list", members_page([user("U0GONEAAAA", "Ann", deleted=True), user("U0BOTAAAAA", "Ann", is_bot=True)]))

        data = failure(await slack.send_direct_message("Ann", "hello"))

        assert "not found" in data["error"]

    async def test_empty_directory_page_with_a_cursor_is_read_past(self, slack, api) -> None:
        api.on("users.list", members_page([], "c2"), members_page([ANN]))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        ok, _ = result(await slack.send_direct_message("Ann", "hello"))

        assert ok is True
        assert len(api.called("users.list")) == 2
        assert api.called("conversations.open")[0].args["users"] == ANN["id"]

    async def test_mention_that_cannot_be_looked_up_is_left_as_typed(self, slack, api) -> None:
        api.on("users.list", rate_limited())
        api.on("chat.postMessage", {"ts": "1.1"})

        ok, _ = result(await slack.send_message_with_mentions(GENERAL, "ping @Ann", mentions=["Ann"]))

        assert ok is True
        assert api.called("chat.postMessage")[0].args["text"] == "ping @Ann"

    async def test_channel_list_that_is_not_a_list_is_refused(self, slack, api) -> None:
        failure(await slack.send_message_to_multiple_channels(42, "hi"))  # type: ignore[arg-type]

        assert api.calls == []

    async def test_unknown_channel_name_is_passed_on_for_slack_to_reject(self, slack, api) -> None:
        api.on("conversations.list", {"channels": [{"id": RANDOM, "name": "random"}], "response_metadata": {"next_cursor": ""}})
        api.on("chat.postMessage", slack_error("channel_not_found"))

        data = failure(await slack.send_message("#nope", "hi"))

        assert api.called("chat.postMessage")[0].args["channel"] == "#nope"
        assert "fetch_channels" in explanation(data)


class TestSearchAndFiles:
    async def test_search_all_returns_messages_and_files_with_names(self, slack, api) -> None:
        api.on("search.messages", {"messages": {"matches": [{"ts": "1.1", "user": ANN["id"], "text": "launch", "channel": {"id": GENERAL}}]}, "files": {"matches": [{"id": "F1", "user": SAM["id"], "name": "plan.pdf"}]}})
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})
        api.on("conversations.info", {"channel": {"id": GENERAL, "name": "general"}})

        ok, data = result(await slack.search_all("launch", limit=3))

        assert ok is True
        assert api.called("search.messages")[0].args == {"query": "launch", "count": "3"}
        assert data["data"]["messages"]["matches"][0]["user_display_name"] == "Ann"

    async def test_reactions_on_a_file_comment_get_names(self, slack, api) -> None:
        api.on("reactions.get", {"type": "file_comment", "file_comment": {"id": "Fc1", "user": ANN["id"], "reactions": [{"name": "tada", "users": [SAM["id"]]}]}})
        api.on("users.info", lambda args: {"user": ANN if args["user"] == ANN["id"] else SAM})

        ok, data = result(await slack.get_reactions(GENERAL, "1.1"))

        assert ok is True
        assert "Sam" in json.dumps(data["data"]["file_comment"])

    async def test_search_all_timeline_summary_counts_both_kinds(self) -> None:
        from types import SimpleNamespace

        from app.agents.actions.slack.slack import _search_all_result_summary

        content = json.dumps({"data": {"messages": {"matches": [{"user_display_name": "Ann", "text": "launch plan"}]}, "files": {"matches": [{}, {}]}}})
        summary = _search_all_result_summary({}, SimpleNamespace(is_error=False, content=content))
        failed = _search_all_result_summary({}, SimpleNamespace(is_error=True, content=json.dumps({"error": "ratelimited"})))

        assert summary.startswith("Found 1 message and 2 files")
        assert "Ann: launch plan" in summary
        assert failed.startswith("Failed:")


class TestDirectoryReadsThatDidNotFinish:
    async def test_partial_match_on_page_one_is_not_messaged_when_page_two_failed(self, slack, api) -> None:
        # The unread page may hold the real "Sam"; Samantha must not get the message.
        api.on("users.list", members_page([SAMANTHA, ANN], "c2"), rate_limited(retry_after=9))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        data = failure(await slack.send_direct_message("Sam", "your review is due"))

        assert "9 seconds" in explanation(data)
        assert api.called("conversations.open") == []
        assert api.called("chat.postMessage") == []

    async def test_exact_match_on_page_one_is_not_messaged_when_page_two_failed(self, slack, api) -> None:
        # Page two could hold a second "Sam", which would make the name ambiguous.
        api.on("users.list", members_page([SAM], "c2"), slack_error("internal_error", status=500))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        failure(await slack.send_direct_message("Sam", "hello"))

        assert api.called("chat.postMessage") == []

    async def test_search_users_with_an_unread_page_and_no_match_is_a_failure(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "c2"), rate_limited(retry_after=4))

        data = failure(await slack.search_users("zoe"))

        assert "4 seconds" in explanation(data)

    async def test_search_users_with_an_unread_page_says_the_matches_are_partial(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "c2"), rate_limited())

        ok, data = result(await slack.search_users("ann"))

        assert ok is True
        assert [u["id"] for u in data["data"]["users"]] == [ANN["id"]]
        assert data["data"]["complete"] is False
        assert "only part" in data["message"]


class TestPartialListsKeepTheirGuidance:
    async def test_channel_members_cut_short_say_so_with_the_wait(self, slack, api) -> None:
        api.on("conversations.members", {"members": [ANN["id"]], "response_metadata": {"next_cursor": "c2"}}, rate_limited(retry_after=7))
        api.on("users.info", {"user": ANN})

        ok, data = result(await slack.get_channel_members_by_id(GENERAL))

        assert ok is True
        assert data["data"]["complete"] is False
        assert "only part" in data["message"] and "7 seconds" in data["message"]

    async def test_list_cut_short_by_a_rejected_sign_in_says_to_reconnect(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "c2"), slack_error("invalid_auth"))

        ok, data = result(await slack.get_users_list())

        assert ok is True
        assert "only part" in data["message"] and "Reconnect the Slack toolset" in data["message"]

    async def test_partial_search_keeps_the_permission_guidance(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "c2"), slack_error("missing_scope"))

        ok, data = result(await slack.search_users("ann"))

        assert ok is True
        assert "only part" in data["message"] and "permission" in data["message"]

    async def test_a_cursor_slack_repeats_ends_the_listing_as_incomplete(self, slack, api) -> None:
        api.on("conversations.list", {"channels": [{"id": GENERAL, "name": "general"}], "response_metadata": {"next_cursor": "same"}})

        ok, data = result(await slack.fetch_channels())

        assert ok is True
        assert len(api.called("conversations.list")) == 2
        assert data["data"]["complete"] is False


class TestCursorsThatNeverEnd:
    async def test_repeated_directory_cursor_picks_no_recipient_and_stops(self, slack, api) -> None:
        api.on("users.list", members_page([SAMANTHA], "same"))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        data = failure(await slack.send_direct_message("Sam", "your review is due"))

        assert data["error"] == "user_lookup_failed"
        assert len(api.called("users.list")) == 2
        assert api.called("conversations.open") == []
        assert api.called("chat.postMessage") == []

    async def test_empty_page_does_not_end_the_directory_before_a_namesake(self, slack, api) -> None:
        other_sam = user("U0SAMTWO00", "Sam", "sam.two@example.com")
        api.on("users.list", members_page([SAM], "c2"), members_page([], "c3"), members_page([other_sam]))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        data = failure(await slack.send_direct_message("Sam", "hello"))

        assert "Multiple users" in data["error"]
        assert api.called("chat.postMessage") == []

    async def test_repeated_channel_list_cursor_stops_resolving_the_name(self, slack, api) -> None:
        api.on("conversations.list", {"channels": [{"id": RANDOM, "name": "random"}], "response_metadata": {"next_cursor": "same"}})
        api.on("chat.postMessage", slack_error("channel_not_found"))

        failure(await slack.send_message("#general", "hi"))

        assert len(api.called("conversations.list")) == 2
        assert api.called("chat.postMessage")[0].args["channel"] == "#general"


class TestPageSizes:
    async def test_no_list_call_asks_for_1000_items_a_page(self, slack, api) -> None:
        # Slack's docs require the limit to be under 1000 for conversations.list and users.conversations.
        api.on("auth.test", {"user_id": ME})
        api.on("conversations.list", {"channels": [{"id": GENERAL, "name": "general"}], "response_metadata": {"next_cursor": ""}})
        api.on("users.conversations", {"channels": [], "response_metadata": {"next_cursor": ""}})
        api.on("conversations.members", {"members": [], "response_metadata": {"next_cursor": ""}})
        api.on("users.list", members_page([ANN]))
        api.on("conversations.open", {"channel": {"id": DM}})
        api.on("chat.postMessage", {"ts": "1.1"})

        await slack.fetch_channels()
        await slack.get_user_channels()
        await slack.get_channel_members("#general")
        await slack.send_direct_message("Ann", "hi")
        await slack.search_users("ann")

        limits = [(c.method, int(c.args["limit"])) for c in api.calls if "limit" in c.args]
        assert {method for method, _ in limits} >= {"conversations.list", "users.conversations", "conversations.members", "users.list"}
        assert all(0 < value < 1000 for _, value in limits), limits


class TestZeroLimits:
    async def test_users_limit_of_zero_reads_nothing(self, slack, api) -> None:
        api.on("users.list", members_page([ANN]))

        ok, data = result(await slack.get_users_list(limit=0))

        assert ok is True
        assert data["data"]["count"] == 0 and data["data"]["complete"] is True
        assert api.called("users.list") == []

    async def test_conversations_limit_of_zero_reads_nothing(self, slack, api) -> None:
        api.on("auth.test", {"user_id": ME})
        api.on("users.conversations", {"channels": [{"id": GENERAL}], "response_metadata": {"next_cursor": ""}})

        ok, data = result(await slack.get_user_conversations(limit=0))

        assert ok is True
        assert data["data"]["count"] == 0
        assert api.called("users.conversations") == []


class TestRepeatedPagesDoNotFillLimits:
    async def test_limit_met_only_by_a_repeated_page_is_not_complete(self, slack, api) -> None:
        api.on("users.list", members_page([ANN], "same"))

        ok, data = result(await slack.get_users_list(limit=2))

        assert ok is True
        assert [m["id"] for m in data["data"]["members"]] == [ANN["id"]]
        assert data["data"]["complete"] is False
        assert len(api.called("users.list")) == 2

    async def test_unlimited_listing_leaves_out_the_repeated_page(self, slack, api) -> None:
        api.on("conversations.list", {"channels": [{"id": GENERAL, "name": "general"}], "response_metadata": {"next_cursor": "same"}})

        ok, data = result(await slack.fetch_channels())

        assert ok is True
        assert [c["id"] for c in data["data"]["channels"]] == [GENERAL]
        assert data["data"]["complete"] is False
