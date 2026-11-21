"""The wrap-up request: the answer-only final turn's history cut to its reserve (BUDGETEND).

A run past its spending limit answers on one turn paid from its wrap-up reserve, so that request has a fixed
size whatever the history grew to: never refused for size, never more than the reserve. Reductions, cheapest
first, until the request fits:

1. every tool result cut to a smaller share (both ends kept, as the per-result cap cuts), down to a floor;
2. the oldest turns (a response with its results) dropped whole, keeping the newest one.

The user's messages (the run's input, a compaction's summary, notes) are never dropped, and a call is never
sent without its result. Deterministic: the same history and size give the same request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.context.results import cap_result
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage
from harness_core.kernel.core.tokens import count_tokens

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.core.messages import Message

# Per-result caps tried in order (characters), then turns are dropped at the floor.
WRAP_UP_RESULT_CAPS = (16_000, 8_000, 4_000, 2_000, 1_000)


def fit_wrap_up(
    messages: Sequence[Message], *, input_index: int | None, history_tokens: int
) -> tuple[list[Message], int | None]:
    """``messages`` within ``history_tokens`` (the reserve's prompt less the system prompt and tools), and where
    the run's input is in them; unchanged when they already fit."""
    fitted = list(messages)
    own = fitted[input_index] if input_index is not None and 0 <= input_index < len(fitted) else None
    if count_tokens(fitted) <= history_tokens:
        return fitted, input_index
    for cap in WRAP_UP_RESULT_CAPS:
        fitted = [cap_result(m, cap) if isinstance(m, ToolMessage) else m for m in messages]
        if count_tokens(fitted) <= history_tokens:
            return fitted, _index_of(fitted, own)
    while count_tokens(fitted) > history_tokens:
        groups = _turn_groups(fitted)
        # The newest turn stays: its results are what the answer is most likely to need.
        if len(groups) <= 1:
            break
        start, end = groups[0]
        fitted = [*fitted[:start], *fitted[end:]]
    return fitted, _index_of(fitted, own)


def _turn_groups(messages: Sequence[Message]) -> list[tuple[int, int]]:
    """``[start, end)`` of each response with the results that answer it, in order."""
    groups: list[tuple[int, int]] = []
    i = 0
    while i < len(messages):
        if isinstance(messages[i], AssistantMessage):
            end = i + 1
            while end < len(messages) and isinstance(messages[end], ToolMessage):
                end += 1
            groups.append((i, end))
            i = end
        else:
            i += 1
    return groups


def _index_of(messages: Sequence[Message], own: Message | None) -> int | None:
    if own is None:
        return None
    return next((i for i, m in enumerate(messages) if m is own), None)


__all__ = ["WRAP_UP_RESULT_CAPS", "fit_wrap_up"]
