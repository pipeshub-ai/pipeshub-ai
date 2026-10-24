from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.context.watermark import watermarks
from app.agent_loop_lib.core.tokens import CHARS_PER_TOKEN, count_message_tokens, count_tokens
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


def _cap_parts(msg, max_result_chars: int, truncate):
    """Cap the text parts of a multipart tool message, leaving images alone.

    Images are bounded by their own admission path; what runs away here is the
    text a large record renders to.
    """
    total = sum(
        len(p.text) for p in msg.content
        if getattr(p, "type", None) == "text" and isinstance(getattr(p, "text", None), str)
    )
    if total <= max_result_chars:
        return msg

    capped = []
    remaining = max_result_chars
    for part in msg.content:
        if getattr(part, "type", None) != "text" or not isinstance(getattr(part, "text", None), str):
            capped.append(part)
            continue
        if len(part.text) <= remaining:
            remaining -= len(part.text)
            capped.append(part)
            continue
        # `remaining`, not the whole allowance: parts kept in full above have
        # already spent part of it, and truncating this one to the full budget
        # is what let a multipart result reach twice the cap.
        capped.append(part.model_copy(update={"text": truncate(part.text, remaining)}))
        remaining = 0
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

    def _truncate(text: str, limit: int = max_result_chars) -> str:
        """Keep both ends, within `limit` characters *including* the marker.

        A tool result carries its instructions last -- the citation rule, the
        continuation hint, which ids were unavailable -- so a tail cut removes
        precisely what the model needs to act on.
        """
        if limit <= 0:
            return ""
        # Budgeted against the widest the dropped count can print, so the real
        # marker always fits and the result never exceeds `limit`.
        keep = limit - len(_marker(len(text)))
        if keep <= 0:
            # No room for both; a fragment of the marker would say nothing.
            return text[:limit]
        keep_head = int(keep * 0.75)
        keep_tail = keep - keep_head
        # Not `text[-keep_tail:]`: at keep_tail == 0 that returns the whole
        # string rather than nothing.
        return (
            text[:keep_head]
            + _marker(len(text) - keep)
            + text[len(text) - keep_tail:]
        )

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
                shaped.append(_cap_parts(msg, limit, _truncate))
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
