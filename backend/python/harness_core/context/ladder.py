"""The reduction ladder (REPORT 6.3, P4.2): which reduction, if any, the next request needs.

Decided from the provider's own count of the last request (``usage``, system and
tools included), never from an estimate of the text: estimates are what let
the legacy shapers disagree with the provider (REPORT C4). One rung per
measurement: a reduction is judged by the next request's usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.context.results import (
    RESULT_CAP_CHARS,
    RESULT_CHARS_PER_TOKEN,
    batch_result_cap,
)
from harness_core.context.types import SeqSpan
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage
from harness_core.kernel.core.tokens import count_message_tokens

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.context.types import Compaction, HistoryItem

ReductionKind = Literal["clear", "compact"]

@dataclass(frozen=True)
class LadderPolicy:
    clear_above: float = 0.5
    compact_above: float = 0.8
    # The most recent turns (assistant responses) stay as they are.
    keep_recent_turns: int = 3
    # ...at most this share of the window between them (BUDGETEND): fewer turns stay, never fewer than one, when
    # their results are large. Without it one run's kept tail alone was 95k-135k tokens of a 128k window.
    keep_recent_share: float | None = 0.25


@dataclass(frozen=True)
class Measurement:
    """The provider's count of one request: the response it came with and its tokens."""

    seq: int
    tokens: int


@dataclass(frozen=True)
class Reduction:
    kind: ReductionKind
    span: SeqSpan


def plan_reduction(
    history: Sequence[HistoryItem],
    *,
    measurement: Measurement | None,
    window_tokens: int,
    compactions: Sequence[Compaction] = (),
    cleared: Sequence[SeqSpan] = (),
    reduced_at_seq: int = 0,
    policy: LadderPolicy = LadderPolicy(),
) -> Reduction | None:
    if measurement is None or window_tokens <= 0 or measurement.seq <= reduced_at_seq:
        return None
    level = measurement.tokens / window_tokens
    old = _before_recent(history, policy.keep_recent_turns, _tail_tokens(policy, window_tokens), window_tokens)
    if not old:
        return None
    if level > policy.compact_above:
        return forced_compaction(history, compactions=compactions, policy=policy, window_tokens=window_tokens)
    if level > policy.clear_above:
        clearable = [
            item
            for item in old
            if _has_results(item)
            and not any(span.covers(item) for span in cleared)
            and not any(c.span.covers(item) for c in compactions)
        ]
        if clearable:
            return Reduction("clear", SeqSpan(clearable[0].first_seq, clearable[-1].last_seq))
    return None


def forced_compaction(
    history: Sequence[HistoryItem],
    *,
    compactions: Sequence[Compaction] = (),
    policy: LadderPolicy = LadderPolicy(),
    window_tokens: int | None = None,
) -> Reduction | None:
    """The compact rung on the provider's own verdict that the request is too large (P4.9): everything
    before the recent turns, unless a compaction already covers it. With ``window_tokens``, the recent turns kept
    are as many as fit ``keep_recent_share`` of it; when a compaction already covers everything before them, the
    rung escalates to keeping the newest turn alone rather than doing nothing (BUDGETEND)."""
    budget = _tail_tokens(policy, window_tokens)
    for keep in dict.fromkeys((policy.keep_recent_turns, 1)):
        old = _before_recent(history, keep, budget, window_tokens)
        if old and not all(any(c.span.covers(i) for c in compactions) for i in old):
            return Reduction("compact", SeqSpan(history[0].first_seq, old[-1].last_seq))
        if budget is None:
            break
    return None


def _tail_tokens(policy: LadderPolicy, window_tokens: int | None) -> int | None:
    if not window_tokens or policy.keep_recent_share is None:
        return None
    return int(window_tokens * policy.keep_recent_share)


def _before_recent(
    history: Sequence[HistoryItem], keep: int, max_tail_tokens: int | None = None, window_tokens: int | None = None
) -> list[HistoryItem]:
    """The items before the ``keep``-th last assistant response, or before a later one when the items from there on
    would take more than ``max_tail_tokens`` (the newest response always stays)."""
    responses = [
        i for i, item in enumerate(history) if any(isinstance(m, AssistantMessage) for m in item.messages)
    ]
    if len(responses) <= keep:
        return []
    cut = responses[-keep] if keep > 0 else len(history)
    if max_tail_tokens is not None and keep > 1:
        for start in responses[-keep:-1]:
            if sum(_item_tokens(item, window_tokens) for item in history[start:]) <= max_tail_tokens:
                break
            cut = responses[responses.index(start) + 1]
    return list(history[:cut])


def _item_tokens(item: HistoryItem, window_tokens: int | None) -> int:
    """What an item takes of a request: its results as the per-result cap leaves them (``batch_result_cap``)."""
    results = [m for m in item.messages if isinstance(m, ToolMessage)]
    cap = batch_result_cap(len(results), window_tokens, RESULT_CAP_CHARS)
    return sum(
        min(len(m.text), cap) // RESULT_CHARS_PER_TOKEN if isinstance(m, ToolMessage) else count_message_tokens(m)
        for m in item.messages
    )


def _has_results(item: HistoryItem) -> bool:
    return item.provenance is None and any(isinstance(m, ToolMessage) for m in item.messages)


__all__ = [
    "LadderPolicy",
    "Measurement",
    "Reduction",
    "ReductionKind",
    "forced_compaction",
    "plan_reduction",
]
