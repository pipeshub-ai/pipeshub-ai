"""Assembling a streamed model reply into one message."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.messages import BaseMessage

MessageT = TypeVar("MessageT", bound="BaseMessage")

# Below this a merge takes a millisecond or two and runs inline. A longer reply
# is merged in a worker thread: the merge is one uninterruptible call, and while
# it runs on the event loop nothing else on that worker is served, /health included.
OFFLOAD_MIN_CHUNKS = 512


def merge_message_chunks(chunks: Sequence[MessageT]) -> MessageT:
    """Every chunk of one streamed reply as a single message.

    Adding chunks one at a time builds a whole new message per step, and each
    of those re-parses the tool-call arguments received so far. A tool call
    streamed in n fragments therefore cost n parses of an ever-longer string;
    20,000 fragments held one CPU core for over a minute. Handing LangChain the
    full list merges and parses once, and gives the same message.
    """
    first, rest = chunks[0], list(chunks[1:])
    return first + rest if rest else first


async def merge_message_chunks_off_loop(chunks: Sequence[MessageT]) -> MessageT:
    """`merge_message_chunks`, run in a worker thread once the reply is long."""
    if len(chunks) >= OFFLOAD_MIN_CHUNKS:
        return await asyncio.to_thread(merge_message_chunks, chunks)
    return merge_message_chunks(chunks)


__all__ = ["OFFLOAD_MIN_CHUNKS", "merge_message_chunks", "merge_message_chunks_off_loop"]
