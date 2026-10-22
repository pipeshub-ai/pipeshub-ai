"""What a Slack record's own text says about the replies its messages have.

The Slack connectors end each message's block with a "Reply Count: N" line
when that message started a thread. The replies themselves are indexed as
their own records (one per burst of a thread), so a reader holding the parent
does not hold the replies, and has to read the thread to see them.

A record's ``hasReplies`` flag was written only for a record holding one
channel message. Records that bundle several channel messages, and the first
record of a thread (which holds its parent message), were stored with it set
to False, so a header said "Has Replies: False" over a message whose text
showed "Reply Count: 7". The blocks are the record of what each message had,
so this module reads them, and older records read correctly without being
synced again.
"""

from __future__ import annotations

import re
from typing import Any, cast

from app.config.constants.arangodb import Connectors

_SLACK_CONNECTORS = frozenset({Connectors.SLACK.value, Connectors.SLACK_WORKSPACE.value})

# The connector appends the count as the block's last line.
_REPLY_COUNT_LINE = re.compile(r"(?:^|\n)Reply Count: (\d+)\s*\Z")

# A thread's burst records are named for it; their messages all belong to it.
_THREAD_BURST_PREFIX = "thread_burst_"


def _value(field: object) -> object:
    return getattr(field, "value", field)


def _is_slack_message(record: dict[str, Any]) -> bool:
    connector = _value(record.get("connector_name"))
    record_type = _value(record.get("record_type"))
    return str(connector or "").upper() in _SLACK_CONNECTORS and record_type == "MESSAGE"


def _message_blocks(record: dict[str, Any]) -> list[dict[str, Any]]:
    containers = record.get("block_containers")
    if not isinstance(containers, dict):
        return []
    blocks = cast("dict[str, Any]", containers).get("blocks")
    if not isinstance(blocks, list):
        return []
    found: list[dict[str, Any]] = []
    for block in cast("list[Any]", blocks):
        if isinstance(block, dict):
            typed = cast("dict[str, Any]", block)
            if typed.get("parent_block_index") is None:
                found.append(typed)
    return found


def message_reply_counts(record: dict[str, Any]) -> list[int]:
    """The reply count of each message in a Slack record that has replies,
    in block order. Empty for a record that is not a Slack message record."""
    if not _is_slack_message(record):
        return []
    counts: list[int] = []
    for block in _message_blocks(record):
        data = block.get("data")
        if not isinstance(data, str):
            continue
        match = _REPLY_COUNT_LINE.search(data)
        if match and int(match.group(1)) > 0:
            counts.append(int(match.group(1)))
    return counts


def replies_note(record: dict[str, Any], read_thread: str) -> str:
    """One header line saying how many replies the record's messages have and
    how to read them, or "" when none has replies.

    `read_thread` is how this reader reads a thread, a verb phrase such as
    "request this record again with mode=thread" (`ReadHints.thread`); empty
    for a read that is already reading the thread, which gets no line.
    """
    counts = message_reply_counts(record) if read_thread else []
    if not counts:
        return ""
    total = sum(counts)
    replies = "reply" if total == 1 else "replies"
    external_id = str(record.get("external_record_id") or "")
    if external_id.startswith(_THREAD_BURST_PREFIX) and len(counts) == 1:
        # The first record of a thread: its parent, then the replies sent
        # within a few minutes of it. Later replies are in the thread's other
        # records, and the count is as of indexing, so the hint stays.
        held = max(len(_message_blocks(record)) - 1, 0)
        return (
            f"* Replies: {total} {replies} to this thread's first message; this record "
            f"holds {held} — {read_thread} to read the whole thread"
        )
    to_whom = "a message in this record" if len(counts) == 1 else f"{len(counts)} messages in this record"
    return (
        f"* Replies: {total} {replies} to {to_whom}, stored as separate records — "
        f"{read_thread} to read them"
    )


def corrected_has_replies(record: dict[str, Any], *, has_replies: bool | None) -> bool:
    """The record's ``hasReplies`` flag, set when any of its messages shows replies."""
    return bool(has_replies) or bool(message_reply_counts(record))
