"""A Slack record says when its messages have replies, and how to read them.

The replies are records of their own. A reader holding the parent message saw
"Has Replies: False" in the header of records that bundle several channel
messages and of a thread's first record, whatever the message's own
"Reply Count: N" line said, and nothing pointed it to the thread.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import Connectors
from app.models.blocks import BlockType
from app.utils.chat_helpers import get_record, record_header_metadata, record_to_text
from app.utils.record_block_selection import API_READ_HINTS, FETCH_RECORD_HINTS
from app.utils.record_reader import read_record, read_thread
from app.utils.slack_replies import (
    corrected_has_replies,
    message_reply_counts,
    replies_note,
)

API_HINT = "request this record again with mode=thread"


def _message(index: int, text: str, replies: int = 0) -> dict:
    data = f"- **A**: {text}"
    if replies:
        data += f"\n\nReply Count: {replies}"
    return {
        "index": index,
        "type": BlockType.TEXT.value,
        "sub_type": "message",
        "parent_index": 0,
        "source_id": f"17000000{index:02d}.000100",
        "data": data,
    }


def _slack(external_id: str, blocks: list[dict], **extra: object) -> dict:
    return {
        "id": f"rec-{external_id}",
        "virtual_record_id": f"v-{external_id}",
        "record_type": "MESSAGE",
        "connector_name": Connectors.SLACK_WORKSPACE.value,
        "external_record_id": external_id,
        "context_metadata": "Record ID: r\nMessage Details:\n* Has Replies: False",
        "block_containers": {
            "blocks": blocks,
            "block_groups": [{
                "index": 0,
                "type": "text_section",
                "sub_type": "burst",
                "children": {"block_ranges": [{"start": 0, "end": len(blocks) - 1}]},
            }],
        },
        **extra,
    }


SINGLE_WITH_REPLIES = _slack("1700000000.000100", [_message(0, "who owns this?", replies=21)])
SINGLE_WITHOUT_REPLIES = _slack("1700000001.000100", [_message(0, "quiet")])
CHANNEL_BURST_ONE_PARENT = _slack(
    "burst_C1_5b483bdb",
    [_message(0, "a"), _message(1, "b", replies=6), _message(2, "c")],
)
CHANNEL_BURST_TWO_PARENTS = _slack(
    "burst_C1_aaaa0000",
    [_message(0, "a", replies=6), _message(1, "b"), _message(2, "c", replies=3)],
)
FIRST_THREAD_BURST = _slack(
    "thread_burst_C1_1700000000.000100_3294893f",
    [_message(0, "root", replies=11), *(_message(i, f"reply {i}") for i in range(1, 10))],
)
LATER_THREAD_BURST = _slack(
    "thread_burst_C1_1700000000.000100_99999999",
    [_message(0, "reply 10"), _message(1, "reply 11")],
)


class TestReplyCounts:
    def test_counts_come_from_the_messages_own_lines(self) -> None:
        assert message_reply_counts(SINGLE_WITH_REPLIES) == [21]
        assert message_reply_counts(CHANNEL_BURST_ONE_PARENT) == [6]
        assert message_reply_counts(CHANNEL_BURST_TWO_PARENTS) == [6, 3]
        assert message_reply_counts(FIRST_THREAD_BURST) == [11]

    def test_a_record_without_replies_has_none(self) -> None:
        assert message_reply_counts(SINGLE_WITHOUT_REPLIES) == []
        assert message_reply_counts(LATER_THREAD_BURST) == []

    def test_only_the_trailing_line_the_connector_writes_counts(self) -> None:
        quoted = _slack("1700000002.000100", [_message(0, "it said Reply Count: 5 yesterday")])
        assert message_reply_counts(quoted) == []

    def test_a_record_from_another_connector_is_left_alone(self) -> None:
        other = {**SINGLE_WITH_REPLIES, "connector_name": "CONFLUENCE"}
        assert message_reply_counts(other) == []
        assert replies_note(other, API_HINT) == ""

    def test_the_flag_is_set_when_a_message_shows_replies(self) -> None:
        assert corrected_has_replies(CHANNEL_BURST_ONE_PARENT, has_replies=False) is True
        assert corrected_has_replies(FIRST_THREAD_BURST, has_replies=None) is True
        assert corrected_has_replies(SINGLE_WITHOUT_REPLIES, has_replies=False) is False
        assert corrected_has_replies(SINGLE_WITHOUT_REPLIES, has_replies=True) is True


class TestRepliesNote:
    def test_single_message_with_replies(self) -> None:
        assert replies_note(SINGLE_WITH_REPLIES, API_HINT) == (
            "* Replies: 21 replies to a message in this record, stored as separate records — "
            "request this record again with mode=thread to read them"
        )

    def test_burst_with_one_parent(self) -> None:
        assert replies_note(CHANNEL_BURST_ONE_PARENT, API_HINT) == (
            "* Replies: 6 replies to a message in this record, stored as separate records — "
            "request this record again with mode=thread to read them"
        )

    def test_burst_with_two_parents(self) -> None:
        assert replies_note(CHANNEL_BURST_TWO_PARENTS, API_HINT).startswith(
            "* Replies: 9 replies to 2 messages in this record, stored as separate records"
        )

    def test_first_record_of_a_thread_says_how_many_it_holds(self) -> None:
        assert replies_note(FIRST_THREAD_BURST, API_HINT) == (
            "* Replies: 11 replies to this thread's first message; this record holds 9 — "
            "request this record again with mode=thread to read the whole thread"
        )

    def test_one_reply(self) -> None:
        one = _slack("1700000003.000100", [_message(0, "x", replies=1)])
        assert replies_note(one, API_HINT).startswith("* Replies: 1 reply to a message")

    def test_no_replies_no_line(self) -> None:
        assert replies_note(SINGLE_WITHOUT_REPLIES, API_HINT) == ""
        assert replies_note(LATER_THREAD_BURST, API_HINT) == ""

    def test_a_reader_already_reading_the_thread_gets_no_line(self) -> None:
        assert replies_note(SINGLE_WITH_REPLIES, "") == ""


class TestHeader:
    def test_each_reader_is_told_its_own_way_to_read_the_thread(self) -> None:
        api = record_header_metadata(CHANNEL_BURST_ONE_PARENT, API_READ_HINTS)
        assistant = record_header_metadata(CHANNEL_BURST_ONE_PARENT, FETCH_RECORD_HINTS)
        assert api.endswith("request this record again with mode=thread to read them")
        assert assistant.endswith("fetch the whole Slack thread for this record to read them")
        # The line continues the record's own message details.
        assert "* Has Replies: False\n* Replies: 6 replies" in api

    def test_the_fetch_tool_phrasing_is_the_longer_one(self) -> None:
        # Frame estimates measure the header with the fetch tool's hints, so
        # the API's phrasing has to fit in the room they hold.
        assert len(FETCH_RECORD_HINTS.thread) >= len(API_READ_HINTS.thread)

    def test_no_line_without_replies(self) -> None:
        header = record_header_metadata(SINGLE_WITHOUT_REPLIES, API_READ_HINTS)
        assert "Replies:" not in header.replace("Has Replies:", "")

    def test_the_record_content_endpoint_says_it(self) -> None:
        text = record_to_text(SINGLE_WITH_REPLIES)
        head, _, _ = text.partition("Record blocks (sorted):")
        assert "* Replies: 21 replies" in head and "mode=thread" in head


class TestReads:
    async def test_a_record_read_points_to_the_thread(self) -> None:
        read = await read_record(CHANNEL_BURST_ONE_PARENT, max_chars=20_000)
        assert "* Replies: 6 replies to a message in this record" in read["content"]
        assert "mode=thread" in read["content"]

    async def test_a_thread_read_does_not_point_to_itself(self) -> None:
        read = await read_thread([FIRST_THREAD_BURST, LATER_THREAD_BURST], max_chars=40_000)
        assert "reply 11" in read["content"]
        assert "* Replies:" not in read["content"]


class TestGetRecordCorrectsTheFlag:
    @staticmethod
    async def _header(blob: dict, has_replies: bool) -> str:
        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=dict(blob))
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "threadId": None, "hasReplies": has_replies, "isReply": False,
        })
        graph = {
            "v-1": {
                "_key": "rec-1",
                "recordType": "MESSAGE",
                "recordName": "general_2026-07-25_07-20-23",
                "version": 1,
                "origin": "CONNECTOR",
                "connectorName": Connectors.SLACK_WORKSPACE.value,
                "connectorId": "conn-1",
                "externalRecordId": blob["external_record_id"],
                "mimeType": "application/blocks",
            },
        }
        out: dict = {}
        await get_record("v-1", out, blob_store, "org-1", graph, graph_provider)
        return out["v-1"]["context_metadata"]

    @pytest.mark.parametrize(
        ("blob", "stored", "shown"),
        [
            # A burst stored before the connector set the flag.
            (CHANNEL_BURST_ONE_PARENT, False, "True"),
            (FIRST_THREAD_BURST, False, "True"),
            (SINGLE_WITH_REPLIES, True, "True"),
            (SINGLE_WITHOUT_REPLIES, False, "False"),
        ],
    )
    async def test_the_header_follows_the_messages(self, blob: dict, stored: bool, shown: str) -> None:
        header = await self._header(blob, stored)
        assert f"* Has Replies: {shown}" in header
