from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.context.watermark import watermarks
from app.agent_loop_lib.core.tokens import (
    CHARS_PER_TOKEN,
    count_message_tokens,
    count_tokens,
)
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Any

    # (result text, cap, the call's arguments) -> the result refitted under the cap, or None.
    Refitter = Callable[[str, int, Mapping[str, Any]], "str | None"]

logger = logging.getLogger(__name__)

LAYER = "budget_reduction"
_MARKER = "\n[…truncated"

# A tool that can size its own output under this cap should, so what it drops
# is its least useful part rather than a character range.
DEFAULT_MAX_RESULT_CHARS = 64_000


def _marker(dropped: int) -> str:
    return f"{_MARKER} {dropped} chars by budget_reduction]\n"


def _cut(length: int, limit: int) -> tuple[int, int, str]:
    """What a both-ends cut of ``length`` characters keeps within ``limit``,
    marker included: the head length, the tail length and the marker.

    A tool result carries its instructions last -- the citation rule, the
    continuation hint, which ids were unavailable -- so a tail cut removes
    precisely what the model needs to act on.
    """
    if limit <= 0:
        return 0, 0, ""
    # Budgeted against the widest the dropped count can print, so the real
    # marker always fits and the result never exceeds `limit`.
    keep = limit - len(_marker(length))
    if keep <= 0:
        # No room for both; a fragment of the marker would say nothing.
        return limit, 0, ""
    keep_head = int(keep * 0.75)
    return keep_head, keep - keep_head, _marker(length - keep)


def _truncate(text: str, limit: int) -> str:
    """``text`` cut at both ends to ``limit`` characters (`_cut`)."""
    head, tail, marker = _cut(len(text), limit)
    # Not `text[-tail:]`: at tail == 0 that returns the whole string rather
    # than nothing.
    return text[:head] + marker + text[len(text) - tail:]


def _cap_parts(msg, max_result_chars: int):
    """Cap the text parts of a multipart tool message, leaving images alone.

    Images are bounded by their own admission path; what runs away here is the
    text a large record renders to. The cut is the string cut applied to the
    parts' text as one: capping part by part spent the whole allowance on an
    oversized first part and emptied the last one, where the closing
    instructions are.
    """
    def is_text(part) -> bool:
        return getattr(part, "type", None) == "text" and isinstance(getattr(part, "text", None), str)

    total = sum(len(p.text) for p in msg.content if is_text(p))
    if total <= max_result_chars:
        return msg

    head, tail, marker = _cut(total, max_result_chars)
    tail_from = total - tail
    capped = []
    offset = 0
    for part in msg.content:
        if not is_text(part):
            capped.append(part)
            continue
        start, end = offset, offset + len(part.text)
        offset = end
        kept = part.text[: max(0, min(end, head) - start)]
        # The marker sits where the head stops, in the part the cut starts in.
        if start <= head < end:
            kept += marker
            marker = ""
        kept += part.text[max(0, tail_from - start):] if end > tail_from else ""
        capped.append(part.model_copy(update={"text": kept}))
    return msg.model_copy(update={"content": capped, "compacted_by": LAYER})


def _fitting_caps(ctx: ModelCallContext) -> dict[int, int]:
    """The most characters each tool result may keep, by index, so that its
    tool-call group fits beside the messages no layer evicts under the
    sliding window's low-water mark.

    The sliding window evicts down to that mark, not to the budget. A result
    capped only at the whole budget is still over the mark, so the window
    evicted the newest result with its call and the model saw none of what it
    had just asked for, call after call. Results of one call share the room
    evenly. Indices with no room at all are left out: the protected messages
    alone are over the mark, and the guard after the window handles that.
    """
    messages = ctx.messages
    low_water = watermarks(ctx.budget, ctx.budget.effective_max_tokens).low_water
    fixed = sum(count_message_tokens(messages[i]) for i in protected_indices(messages))
    caps: dict[int, int] = {}
    for i, msg in enumerate(messages):
        calls = getattr(msg, "tool_calls", None) if msg.role == MessageRole.ASSISTANT else None
        if not calls:
            continue
        call_ids = {call.id for call in calls}
        results = []
        j = i + 1
        while j < len(messages) and messages[j].role == MessageRole.TOOL:
            if getattr(messages[j], "tool_call_id", None) in call_ids:
                results.append(j)
            j += 1
        if not results:
            continue
        share = (low_water - fixed - count_message_tokens(msg)) // len(results)
        for j in results:
            result = messages[j]
            content = result.content
            empty = "" if isinstance(content, str) else [
                p for p in content if getattr(p, "type", None) != "text"
            ]
            # Overhead, footer and images; +1 for the floor division of the
            # combined text, so the capped result never measures over `share`.
            rest = count_message_tokens(result.model_copy(update={"content": empty})) + 1
            if share > rest:
                caps[j] = (share - rest) * CHARS_PER_TOKEN
    return caps


def shape_budget_reduction(
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    tool_caps: Mapping[str, int] | None = None,
    refitters: Mapping[str, Refitter] | None = None,
):
    """Layer 1 (cheapest) context shaper: caps every individual TOOL message's
    content at `max_result_chars`.

    `tool_caps` gives a named tool its own cap: a tool that sizes its output
    for the model's window (fetch_record) is capped at that size instead.
    No cap lets a result's call group exceed what fits beside the protected
    messages under the sliding window's low-water mark (`_fitting_caps`), or
    the whole message budget when nothing does.

    `refitters` lets a tool whose result describes itself (a page with a
    continuation offset) shorten that result in a way that keeps the
    description true; the both-ends cut below would contradict it.

    Registered on `HookRegistry.on(HookEvent.PRE_MODEL)` — a pure reducer,
    runs unconditionally regardless of total context size, because a single
    oversized tool result (e.g. a full page scrape) is worth capping on its
    own merits. Direct replacement for `BudgetReductionHook`.
    """

    def _calls_by_id(messages) -> dict[str, object]:
        return {
            call.id: call
            for msg in messages if msg.role == MessageRole.ASSISTANT
            for call in getattr(msg, "tool_calls", None) or ()
        }

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        calls = _calls_by_id(ctx.messages) if tool_caps or refitters else {}
        # Later layers never shorten a protected result (context/protection.py),
        # so this is what keeps the newest one able to fit a small window.
        budget_cap = ctx.budget.effective_max_tokens * CHARS_PER_TOKEN
        fitting = _fitting_caps(ctx)
        shaped = []
        for index, msg in enumerate(ctx.messages):
            if msg.role != MessageRole.TOOL or getattr(msg, "compacted_by", None) == LAYER:
                shaped.append(msg)
                continue
            call = calls.get(getattr(msg, "tool_call_id", None) or "")
            tool_name = getattr(call, "name", "")
            limit = min((tool_caps or {}).get(tool_name, max_result_chars), fitting.get(index, budget_cap))
            # Multipart content (a tool that returned images) used to skip this
            # cap entirely -- the one result shape most likely to be oversized.
            if isinstance(msg.content, list):
                shaped.append(_cap_parts(msg, limit))
                continue
            if not isinstance(msg.content, str):
                shaped.append(msg)
                continue
            # Artifact-bearing messages have their full content safely stored
            # in the artifact store — truncate here like any other tool
            # result.  L2 artifact_compaction handles turn-aware replacement
            # with compact references on later turns; the model can call
            # retrieve_artifact_content for the full data.
            if len(msg.content) <= limit:
                shaped.append(msg)
                continue
            refit = (refitters or {}).get(tool_name)
            content = refit(msg.content, limit, getattr(call, "arguments", None) or {}) if refit else None
            if content is None:
                content = _truncate(msg.content, limit)
            shaped.append(msg.model_copy(update={"content": content, "compacted_by": LAYER}))
        capped = sum(1 for old, new in zip(ctx.messages, shaped) if new is not old)
        if capped:
            logger.info(
                "budget_reduction: capped %d tool result(s), %d→%d tokens (run=%s, turn=%d, budget=%d)",
                capped, count_tokens(ctx.messages), count_tokens(shaped),
                ctx.run_id, ctx.turn_index, ctx.budget.effective_max_tokens,
            )
        ctx.messages = shaped
        await next_fn()

    return _middleware
