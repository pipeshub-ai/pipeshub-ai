"""Final PRE_MODEL layer: the shaped context must still be a valid request.

Runs after every other shaper and enforces what none of them may break:

1. Tool pairing: every tool result follows the call that asked for it
   (see ``_message_boundaries.repair_tool_pairing``).
2. The current request is sent verbatim and is the last human message
   (``context/protection.py``). Every shaper protects it, so a repair here
   is a shaper bug: it is logged at ERROR and the request is put back where
   it belongs, before the run's first surviving tool call.
3. The request fits the model's window, or ``ContextBudgetExceeded`` stops
   the call instead of sending something the provider rejects.
"""

from __future__ import annotations

import logging

from app.agent_loop_lib.context.errors import ContextBudgetExceeded
from app.agent_loop_lib.context.protection import (
    CurrentRequest,
    current_request_index,
    is_human,
)
from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.tokens import count_tokens, extract_text
from app.agent_loop_lib.core.types import Message
from app.agent_loop_lib.hooks.middleware.builtin._message_boundaries import (
    repair_tool_pairing,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)


def _sends_request(messages: list[Message], request: CurrentRequest) -> bool:
    index = current_request_index(messages)
    return index is not None and extract_text(messages[index]) == request.text


def restore_current_request(messages: list[Message], request: CurrentRequest) -> list[Message]:
    """Put the current request back before the run's first surviving tool
    call (or at the end when none survived), dropping any misplaced copy.

    Nothing the person typed comes after the current request, so a
    human-looking message after it was made by a shaper: it is marked
    injected, which keeps the request the last human message."""
    kept = [m for m in messages if not (is_human(m) and extract_text(m) == request.text)]
    insert_at = next(
        (
            i for i, m in enumerate(kept)
            if isinstance(m, AssistantMessage)
            and any(call.id in request.later_call_ids for call in m.tool_calls or ())
        ),
        len(kept),
    )
    after = [m.model_copy(update={"injected": True}) if is_human(m) else m for m in kept[insert_at:]]
    return [*kept[:insert_at], request.message, *after]


def _run_id(ctx: ModelCallContext) -> str | None:
    if ctx.scope is None:
        return None
    return ctx.scope.run.identity.run_id


def shape_context_invariants():
    """Last PRE_MODEL middleware; see the module docstring."""

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        messages, repairs = repair_tool_pairing(ctx.messages)
        if repairs:
            logger.warning(
                "context_invariants: dropped/stripped %d orphaned tool message(s)/call(s) — "
                "a context shaper broke pairing (run=%s, turn=%d)",
                repairs, _run_id(ctx), ctx.turn_index,
            )

        request = ctx.request
        if request is not None and not _sends_request(messages, request):
            messages = restore_current_request(messages, request)
            logger.error(
                "context_invariant_repaired: the shapers lost the current request; restored it "
                "(run=%s, session=%s, turn=%d, messages=%d)",
                _run_id(ctx), ctx.session_id, ctx.turn_index, len(messages),
            )

        total = count_tokens(messages)
        if total > ctx.budget.hard_limit_tokens:
            logger.error(
                "context_budget_exceeded: %d tokens > %d limit after every shaper "
                "(run=%s, turn=%d, budget=%d)",
                total, ctx.budget.hard_limit_tokens, _run_id(ctx), ctx.turn_index,
                ctx.budget.effective_max_tokens,
            )
            raise ContextBudgetExceeded(total, ctx.budget.hard_limit_tokens)

        ctx.messages = messages
        await next_fn()

    return _middleware
