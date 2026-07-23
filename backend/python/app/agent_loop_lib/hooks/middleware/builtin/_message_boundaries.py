"""Shared helpers for keeping tool_call / tool_result message pairs intact
when context-shaping middleware splits a message list into regions.

Two layers of protection:

1. **Prevention** — ``safe_tail_boundary`` adjusts head/middle/tail split
   points so compaction shapers never orphan ToolMessages in the first place
   (information is preserved by moving the boundary).

2. **Repair** — ``repair_tool_pairing`` / ``shape_tool_pairing_repair``
   run as the LAST PRE_MODEL middleware and drop any orphaned ToolMessages
   or strip orphaned tool_calls that slipped through despite layer 1 (e.g.
   because two shapers interacted, or a future shaper doesn't use
   ``safe_tail_boundary``).  Information IS lost here, but the LLM call
   succeeds instead of 400-ing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import MessageRole

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import Message
    from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer 1: prevention — adjust split boundaries
# ---------------------------------------------------------------------------

def safe_tail_boundary(
    messages: list[Message], raw_start: int, lower_bound: int
) -> int:
    """Return a tail-start index that never orphans leading ``ToolMessage``s.

    When a compaction middleware splits messages into ``[head | middle | tail]``
    and replaces the middle with a summary, the tail must not begin with
    ``ToolMessage``s whose parent ``AssistantMessage`` (carrying the matching
    ``tool_calls``) would be inside the replaced region — otherwise the LLM
    provider rejects the request (OpenAI 400: "messages with role 'tool'
    must be a response to a preceding message with 'tool_calls'"; Anthropic
    400: "Each tool_result block must have a corresponding tool_use block").

    Walks backwards from ``raw_start`` past any contiguous ``ToolMessage``s
    and includes the preceding ``AssistantMessage`` with ``tool_calls`` if
    found.  Never walks past ``lower_bound`` (typically ``pin_first_n``).
    """
    if raw_start <= lower_bound or raw_start >= len(messages):
        return raw_start

    i = raw_start
    while i > lower_bound and messages[i].role == MessageRole.TOOL:
        i -= 1

    if i < raw_start and i >= lower_bound:
        msg = messages[i]
        if msg.role == MessageRole.ASSISTANT and getattr(msg, "tool_calls", None):
            return i

    return raw_start


# ---------------------------------------------------------------------------
# Layer 2: repair — final safety net
# ---------------------------------------------------------------------------

def repair_tool_pairing(messages: list[Message]) -> tuple[list[Message], int]:
    """Ensure every ``ToolMessage`` has a **preceding** ``AssistantMessage``
    with a matching ``tool_call``, and vice-versa — enforcing the positional
    constraint that providers (Anthropic, OpenAI) actually validate.

    Position-aware: a ``ToolMessage`` is only valid if the most recent
    ``AssistantMessage`` before it (walking backwards, skipping other
    ``ToolMessage``s and ``UserMessage``s in between) contains a
    ``tool_call`` with a matching ID.  Global ID presence is NOT sufficient
    — a ToolMessage referencing a tool_call from an *earlier* assistant
    group is invalid once compaction removes intervening structure.

    Returns ``(repaired_messages, repair_count)``.
    """
    # --- Pass 1: position-aware drop of orphaned ToolMessages ---
    # Walk forward, tracking the "active" tool_call IDs from the most
    # recent AssistantMessage that has tool_calls.
    active_call_ids: set[str] = set()
    surviving: list[Message] = []
    repairs = 0

    for msg in messages:
        if msg.role == MessageRole.ASSISTANT and getattr(msg, "tool_calls", None):
            active_call_ids = {tc.id for tc in msg.tool_calls}
            surviving.append(msg)
        elif msg.role == MessageRole.TOOL:
            tc_id = getattr(msg, "tool_call_id", None)
            if not tc_id or tc_id not in active_call_ids:
                repairs += 1
                continue
            surviving.append(msg)
        else:
            surviving.append(msg)

    # --- Pass 2: strip orphaned tool_calls whose ToolMessages were dropped ---
    answered_ids: set[str] = set()
    for msg in surviving:
        if msg.role == MessageRole.TOOL:
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id:
                answered_ids.add(tc_id)

    result: list[Message] = []
    for msg in surviving:
        if (
            msg.role == MessageRole.ASSISTANT
            and getattr(msg, "tool_calls", None)
        ):
            kept = [tc for tc in msg.tool_calls if tc.id in answered_ids]
            if len(kept) < len(msg.tool_calls):
                repairs += len(msg.tool_calls) - len(kept)
                msg = msg.model_copy(update={"tool_calls": kept or None})
        result.append(msg)

    return result, repairs


def shape_tool_pairing_repair():
    """**Final** PRE_MODEL middleware: enforces the tool-pairing invariant.

    Both OpenAI and Anthropic reject messages where a ``tool``/``tool_result``
    has no preceding ``tool_calls``/``tool_use``, or vice-versa.  Earlier
    shapers (loop_compaction, sliding_window, auto_compact, …) use
    ``safe_tail_boundary`` and group-eviction to *prevent* orphans, but
    shaper interactions or future shapers can still produce them.  This
    middleware is the catch-all: if an orphan slipped through, drop it and
    log a warning so the underlying shaper can be fixed.
    """

    async def _middleware(ctx: "ModelCallContext", next_fn) -> None:
        repaired, count = repair_tool_pairing(ctx.messages)
        if count:
            logger.warning(
                "tool_pairing_repair: dropped/stripped %d orphaned tool "
                "message(s)/call(s) — a context shaper broke pairing",
                count,
            )
            ctx.messages = repaired
        await next_fn()

    return _middleware
