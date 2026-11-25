# pyright: basic
"""How much one model call may produce (P4.17).

Two bounds, both needed:

- **A limit on the wire**, per call purpose. A reasoning model's runaway is hidden reasoning: the stream shows
  nothing for minutes, so no client-side count can stop it, and only the provider's own output limit does. The
  limits come from the output sizes measured on recorded model runs (``impl/P4.17-output-bound.md``, Limits), and a model's
  published maximum caps each.
- **Degenerate repetition stopped in the stream.** A visible loop (the same unit again and again) is cut as soon as
  it is unambiguous. It is a typed truncation (``cut == "repetition"``) that the loop's recovery continues, never a
  silent end.

The bound never ends a call as an error: the response is a truncated turn, so both loops' recovery (continue,
answer now, retry a cut tool call smaller) applies.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.kernel.budget.pricing import max_output_tokens
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.streaming import StreamEvent

OutputPurpose = Literal["turn", "structured", "summary", "engine"]

# About 3x the largest deliverable call measured (10,598 tokens, reasoning included; p99 of the deliverable call
# 9,393), so a legitimate long answer or document is never cut, while a runaway stops at a quarter of the 128k
# ceiling it reached.
DEFAULT_TURN_LIMIT = 32_768
# Structured calls and summaries return short documents: the largest structured call measured is 1,218 tokens
# (judge calls, p99 794). Reasoning comes on top, so the bound leaves room for it.
DEFAULT_STRUCTURED_LIMIT = 8_192
DEFAULT_SUMMARY_LIMIT = 8_192
# What a thinking budget leaves for the visible reply when it has to shrink to fit the limit.
VISIBLE_HEADROOM = 4_096
# Anthropic's and Bedrock's floor for an enabled thinking budget.
MIN_THINKING_BUDGET = 1_024

REPETITION = "repetition"
OUTPUT_REPETITION = "output_repetition"
CutReason = Literal["repetition"]


@dataclass(frozen=True)
class OutputLimits:
    """The output limit of each call purpose, in tokens; ``None`` leaves that purpose to the provider's maximum."""

    turn: int | None = DEFAULT_TURN_LIMIT
    structured: int | None = DEFAULT_STRUCTURED_LIMIT
    summary: int | None = DEFAULT_SUMMARY_LIMIT
    engine: int | None = DEFAULT_TURN_LIMIT
    # Stop a stream that loops (``bound_stream``); off only with every limit, so ``OFF`` is the behaviour before.
    repetition: bool = True

    @classmethod
    def from_turn_limit(cls, turn: int | None) -> OutputLimits:
        """The limits an operator's one number sets: turn and engine calls at it, the smaller purposes at theirs
        (never above it). ``None`` or ``0``: no wire limit anywhere."""
        if not turn:
            return OFF
        return cls(
            turn=turn,
            structured=min(DEFAULT_STRUCTURED_LIMIT, turn),
            summary=min(DEFAULT_SUMMARY_LIMIT, turn),
            engine=turn,
        )

    def for_call(self, purpose: OutputPurpose, model: str | None) -> int | None:
        """The limit of one call: the purpose's, never above the model's published maximum."""
        limit: int | None = getattr(self, purpose)
        if limit is None:
            return None
        known = max_output_tokens(model)
        return min(limit, known) if known else limit

    def for_engine(self, requested: int | None, model: str | None) -> int | None:
        """An engine's request through the model gateway: its own limit when smaller, else the engine limit."""
        limit = self.for_call("engine", model)
        if requested is None or requested <= 0:
            return limit
        return requested if limit is None else min(requested, limit)


OFF = OutputLimits(turn=None, structured=None, summary=None, engine=None, repetition=False)


def output_limit(limit: int | None) -> dict[str, int]:
    """The ``max_output_tokens`` keyword a wrapper forwards, only when a limit was given."""
    return {} if limit is None else {"max_output_tokens": limit}


def within(configured: int | None, limit: int | None) -> int | None:
    """The output cap a transport sends: a limit lowers a configured cap, never raises it."""
    if limit is None:
        return configured
    return limit if configured is None else min(configured, limit)


def thinking_within(limit: int | None, budget: int | None) -> int | None:
    """A thinking budget that leaves the visible reply room inside ``limit`` (Anthropic and Bedrock count thinking
    in ``max_tokens``): lowered to ``limit`` minus a visible headroom, never below the providers' floor."""
    if limit is None or budget is None:
        return budget
    headroom = min(VISIBLE_HEADROOM, limit // 4)
    return max(MIN_THINKING_BUDGET, min(budget, limit - headroom))


# A unit repeated at least this many times over at least ``REPEAT_SPAN`` characters is a loop: no deliverable in
# a recorded model run has 4 KB of one exactly repeating unit, and a loop costs about 1k tokens before it is caught.
REPEAT_SPAN = 4_096
MIN_REPEATS = 8
CHECK_EVERY = 256


class RepetitionWatch:
    """Finds a stream whose last ``span`` characters are one unit repeated ``MIN_REPEATS`` or more times.

    Checked every ``every`` characters on a bounded tail, so the cost per character is constant; equal-length
    string comparisons stop at the first difference, so ordinary text costs a few characters per period tried.
    """

    def __init__(self, span: int = REPEAT_SPAN, every: int = CHECK_EVERY, min_repeats: int = MIN_REPEATS) -> None:
        super().__init__()
        self._span = span
        self._every = every
        self._max_period = span // min_repeats
        self._tail = ""
        self._since = 0

    def feed(self, delta: str) -> int | None:
        """The repeating unit's length once the stream is a loop, else ``None``."""
        if not delta:
            return None
        self._tail = (self._tail + delta)[-(self._span + self._max_period) :]
        self._since += len(delta)
        if self._since < self._every:
            return None
        self._since = 0
        return periodic(self._tail, self._span, self._max_period)


def periodic(tail: str, span: int, max_period: int) -> int | None:
    """The smallest period ``p ≤ max_period`` with which ``tail``'s last ``span + p`` characters repeat."""
    if len(tail) <= span:
        return None
    window = tail[-span:]
    for p in range(1, min(max_period, len(tail) - span) + 1):
        if window == tail[-span - p : -p]:
            return p
    return None


def before_loop(text: str, period: int) -> str:
    """``text`` up to where its trailing loop of ``period`` began, keeping one unit of it."""
    start = len(text) - period
    while start > 0 and text[start - 1] == text[start - 1 + period]:
        start -= 1
    return text[: start + period]


def text_loop(text: str) -> int | None:
    """The period of a loop ``text`` ends in, for a response that was not streamed."""
    return periodic(text, REPEAT_SPAN, REPEAT_SPAN // MIN_REPEATS)


@dataclass(frozen=True)
class OutputCut:
    reason: CutReason
    channel: Literal["text", "reasoning", "tool_call"]
    period: int
    chars: int


def cut_response(
    text: str,
    calls: dict[int, tuple[str | None, str | None]],
    model: str,
    cut: OutputCut,
) -> ModelResponse:
    """The response a cut stream ends with: a truncated turn whose tool calls keep their id and name only (their
    arguments are incomplete and must never run), and no provider usage (the ledger settles at its estimate)."""
    tool_calls = [
        ToolCall(id=call_id or f"call_{index}", name=name)
        for index, (call_id, name) in calls.items()
        if name
    ]
    message = AssistantMessage(
        content=[TextPart(text=text)] if text else [],
        tool_calls=tool_calls or None,
        truncated=True,
        cut=cut.reason,
    )
    return ModelResponse(message=message, usage=TokenUsage(), stop_reason=StopReason.MAX_TOKENS, model=model)


async def bound_stream(
    stream: AsyncIterator[StreamEvent], *, model: str, log: HarnessLog | None = None
) -> AsyncIterator[StreamEvent]:
    """``stream``'s events, ended as a truncated turn once its text, reasoning or one tool call's arguments loop.
    The inner stream is closed however this ends, so the provider stops generating (and billing)."""
    text_watch, reasoning_watch = RepetitionWatch(), RepetitionWatch()
    call_watches: dict[int, RepetitionWatch] = {}
    text: list[str] = []
    calls: dict[int, tuple[str | None, str | None]] = {}
    chars = 0
    try:
        async for event in stream:
            period: int | None = None
            channel: Literal["text", "reasoning", "tool_call"] = "text"
            if isinstance(event, TextDeltaEvent):
                text.append(event.delta)
                chars += len(event.delta)
                period = text_watch.feed(event.delta)
            elif isinstance(event, ThinkingDeltaEvent):
                chars += len(event.delta)
                period, channel = reasoning_watch.feed(event.delta), "reasoning"
            elif isinstance(event, ToolCallDeltaEvent):
                known_id, known_name = calls.get(event.index, (None, None))
                calls[event.index] = (known_id or event.id, known_name or event.name)
                chars += len(event.arguments_delta)
                watch = call_watches.setdefault(event.index, RepetitionWatch())
                period, channel = watch.feed(event.arguments_delta), "tool_call"
            yield event
            if isinstance(event, StreamCompleteEvent):
                return
            if period is not None:
                cut = OutputCut(REPETITION, channel, period, chars)
                kept = "".join(text)
                if channel == "text":
                    kept = before_loop(kept, period)
                (log or _LOG).warning(
                    "model.output_cut",
                    reason=OUTPUT_REPETITION,
                    channel=channel,
                    period=period,
                    chars=chars,
                    model=model,
                )
                yield StreamCompleteEvent(response=cut_response(kept, calls, model, cut))
                return
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


def bound_response(response: ModelResponse, *, log: HarnessLog | None = None) -> ModelResponse:
    """A non-streamed response whose text ends in a loop, trimmed and marked as a cut turn."""
    message = response.message
    if message.cut is not None:
        return response
    text = message.text
    period = text_loop(text)
    if period is None:
        return response
    (log or _LOG).warning(
        "model.output_cut", reason=OUTPUT_REPETITION, channel="text", period=period, chars=len(text),
        model=response.model,
    )
    kept = before_loop(text, period)
    trimmed = message.model_copy(
        update={"content": [TextPart(text=kept)], "truncated": True, "cut": REPETITION}
    )
    return response.model_copy(update={"message": trimmed, "stop_reason": StopReason.MAX_TOKENS})


# Shown with an answer the run could not finish: the user knows it is incomplete and how to get the rest.
SHORTENED_NOTICE = (
    "_This answer was cut short because it reached the length limit for one reply. "
    "Ask me to continue and I will pick up where it stopped._"
)


def shortened(answer: str) -> str:
    """An answer that is still cut when the run has no turn left, never passed off as whole."""
    return f"{answer.rstrip()}\n\n{SHORTENED_NOTICE}"


_LOG = HarnessLog.named("harness_core.models.output_bound")


__all__ = [
    "DEFAULT_STRUCTURED_LIMIT",
    "DEFAULT_SUMMARY_LIMIT",
    "DEFAULT_TURN_LIMIT",
    "OFF",
    "OUTPUT_REPETITION",
    "REPETITION",
    "SHORTENED_NOTICE",
    "CutReason",
    "OutputCut",
    "OutputLimits",
    "OutputPurpose",
    "RepetitionWatch",
    "before_loop",
    "bound_response",
    "bound_stream",
    "cut_response",
    "output_limit",
    "periodic",
    "shortened",
    "text_loop",
    "thinking_within",
    "within",
]
