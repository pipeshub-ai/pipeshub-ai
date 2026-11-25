from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.context.watermark import watermarks
from app.agent_loop_lib.core.messages import ToolMessage
from app.agent_loop_lib.core.tokens import (
    CHARS_PER_TOKEN,
    count_message_tokens,
    count_tokens,
)
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
from harness_core.context.results import RESULT_CAP_CHARS, batch_result_cap, cap_result

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Any

    # (result text, cap, the call's arguments) -> the result refitted under the cap, or None.
    Refitter = Callable[[str, int, Mapping[str, Any]], "str | None"]

logger = logging.getLogger(__name__)

LAYER = "budget_reduction"

# A tool that can size its own output under this cap should, so what it drops
# is its least useful part rather than a character range.
DEFAULT_MAX_RESULT_CHARS = RESULT_CAP_CHARS


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

    def _caps_by_call_id(messages, window: int | None) -> tuple[dict[str, int], dict[str, object]]:
        # A batch's results share a slice of the window (BUDGETEND): many calls at once each get less.
        caps: dict[str, int] = {}
        calls_by_id: dict[str, object] = {}
        for msg in messages:
            if msg.role == MessageRole.ASSISTANT:
                calls = getattr(msg, "tool_calls", None) or ()
                shared = batch_result_cap(len(calls), window, max_result_chars)
                for call in calls:
                    own = tool_caps.get(getattr(call, "name", "")) if tool_caps else None
                    caps[call.id] = own if own is not None else shared
                    calls_by_id[call.id] = call
        return caps, calls_by_id

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        budget = ctx.budget
        window = budget.max_tokens + budget.reserved_prompt_tokens + budget.reserved_output_tokens
        caps, calls = _caps_by_call_id(ctx.messages, window)
        # Later layers never shorten a protected result (context/protection.py),
        # so this is what keeps the newest one able to fit a small window.
        budget_cap = budget.effective_max_tokens * CHARS_PER_TOKEN
        fitting = _fitting_caps(ctx)
        shaped = []
        for index, msg in enumerate(ctx.messages):
            if (
                msg.role != MessageRole.TOOL
                or not isinstance(msg, ToolMessage)
                or msg.compacted_by == LAYER
            ):
                shaped.append(msg)
                continue
            call_id = msg.tool_call_id or ""
            call = calls.get(call_id)
            tool_name = getattr(call, "name", "")
            limit = min(caps.get(call_id, max_result_chars), fitting.get(index, budget_cap))
            refit = (refitters or {}).get(tool_name)
            if refit is not None and isinstance(msg.content, str) and len(msg.content) > limit:
                content = refit(msg.content, limit, getattr(call, "arguments", None) or {})
                if content is not None:
                    shaped.append(msg.model_copy(update={"content": content, "compacted_by": LAYER}))
                    continue
            # Artifact-bearing results are cut like any other (L2 replaces them with references
            # on later turns); a result this shaper already cut is not cut twice, but a foreign
            # marker (retrieve_artifact_content's) grants no pass. Images are left alone.
            capped = cap_result(msg, limit)
            shaped.append(capped if capped is msg else capped.model_copy(update={"compacted_by": LAYER}))
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
