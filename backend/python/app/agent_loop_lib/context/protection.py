"""Which messages the context shapers may never drop, summarise or shorten.

Protection follows a message's role in the run, not its position. Shapers used
to keep "the first message" verbatim, which is the goal of a single-question
run but, in a chat, the conversation's FIRST question: history is seeded
oldest-first ahead of the current request. Under budget pressure the current
question was then summarised or evicted and the model answered the old one.

Protected on every call:

- the current request: the last user message the loop did not inject.
  Earlier turns' questions come before it, and everything the loop adds
  itself (summaries, nudges, plans) is marked ``injected``;
- any message marked ``pinned`` (e.g. the plan a plan-execute run follows);
- system messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.core.tokens import extract_text
from app.agent_loop_lib.core.types import Message, MessageRole


@dataclass(frozen=True, slots=True)
class CurrentRequest:
    """The current request as it stood before any shaper ran on this call."""

    message: UserMessage
    # Tool calls the run made after the request. They survive compaction
    # (shapers keep tool-call ids), so they locate where the request belongs
    # if a shaper ever loses it.
    later_call_ids: frozenset[str]

    @property
    def text(self) -> str:
        return extract_text(self.message)


def is_human(message: Message) -> bool:
    """A user message the person typed, not one the loop injected."""
    return isinstance(message, UserMessage) and not message.injected


def current_request_index(messages: Sequence[Message]) -> int | None:
    """Index of the current request: the last human user message, if any."""
    for i in range(len(messages) - 1, -1, -1):
        if is_human(messages[i]):
            return i
    return None


def current_request(messages: Sequence[Message]) -> CurrentRequest | None:
    index = current_request_index(messages)
    if index is None:
        return None
    later_call_ids = frozenset(
        call.id
        for msg in messages[index + 1:]
        if isinstance(msg, AssistantMessage)
        for call in msg.tool_calls or ()
    )
    return CurrentRequest(message=cast(UserMessage, messages[index]), later_call_ids=later_call_ids)


def protected_indices(messages: Sequence[Message]) -> frozenset[int]:
    """Indices no shaper may drop, summarise or shorten."""
    protected = {
        i for i, msg in enumerate(messages)
        if msg.role == MessageRole.SYSTEM or getattr(msg, "pinned", False)
    }
    current = current_request_index(messages)
    if current is not None:
        protected.add(current)
    return frozenset(protected)


def compactable_runs(
    protected: frozenset[int], start: int, stop: int,
) -> list[range]:
    """Maximal index ranges within ``[start, stop)`` that hold no protected index.

    Protected messages are user or system messages, never tool results, so a
    run never splits a tool call from its results except at ``start`` and
    ``stop``, which callers place on group boundaries
    (``_message_boundaries.safe_tail_boundary``).
    """
    runs: list[range] = []
    run_start = start
    for i in range(start, stop):
        if i in protected:
            if i > run_start:
                runs.append(range(run_start, i))
            run_start = i + 1
    if stop > run_start:
        runs.append(range(run_start, stop))
    return runs


__all__ = [
    "CurrentRequest",
    "compactable_runs",
    "current_request",
    "current_request_index",
    "is_human",
    "protected_indices",
]
