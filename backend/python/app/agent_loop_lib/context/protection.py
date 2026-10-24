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
- system messages;
- with ``protected_tools``, every tool-call group (the assistant message and
  its results) that called one of those tools: results whose text the model
  must still read verbatim, such as fetched records carrying ``[refN]``
  citation ids or a plan.

Protected messages are never summarised or shortened, but a tool result is
still capped by budget reduction (L1, never more than the whole message
budget), the one bound that keeps protected content able to fit. When even that does not fit, the sliding window evicts
protected tool groups, oldest first, after every unprotected message: a
citation the model can no longer read is better than a request the provider
rejects. The current request, pinned and system messages are never evicted;
if they alone exceed the window, the final guard raises
``ContextBudgetExceeded``.
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


def current_request_text(messages: Sequence[Message]) -> str | None:
    index = current_request_index(messages)
    return extract_text(messages[index]) if index is not None else None


def protected_tool_indices(messages: Sequence[Message], protected_tools: frozenset[str]) -> frozenset[int]:
    """Indices of every tool-call group that called a tool in ``protected_tools``:
    the assistant message and the results that follow it, kept together so
    no layer separates a call from its result."""
    if not protected_tools:
        return frozenset()
    protected: set[int] = set()
    for i, msg in enumerate(messages):
        if not isinstance(msg, AssistantMessage) or not msg.tool_calls:
            continue
        if not any(call.name in protected_tools for call in msg.tool_calls):
            continue
        call_ids = {call.id for call in msg.tool_calls}
        protected.add(i)
        j = i + 1
        while j < len(messages) and messages[j].role == MessageRole.TOOL:
            if getattr(messages[j], "tool_call_id", None) in call_ids:
                protected.add(j)
            j += 1
    return frozenset(protected)


def protected_indices(
    messages: Sequence[Message], *, protected_tools: frozenset[str] = frozenset(),
) -> frozenset[int]:
    """Indices no shaper may summarise or shorten (see the module docstring)."""
    protected = {
        i for i, msg in enumerate(messages)
        if msg.role == MessageRole.SYSTEM or getattr(msg, "pinned", False)
    }
    current = current_request_index(messages)
    if current is not None:
        protected.add(current)
    return frozenset(protected) | protected_tool_indices(messages, protected_tools)


def compactable_runs(
    protected: frozenset[int], start: int, stop: int,
) -> list[range]:
    """Maximal index ranges within ``[start, stop)`` that hold no protected index.

    A protected tool result is protected with its whole tool-call group, and
    other protected messages are user or system messages, so a run never
    splits a tool call from its results except at ``start`` and ``stop``,
    which callers place on group boundaries
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
    "current_request_text",
    "is_human",
    "protected_indices",
    "protected_tool_indices",
]
