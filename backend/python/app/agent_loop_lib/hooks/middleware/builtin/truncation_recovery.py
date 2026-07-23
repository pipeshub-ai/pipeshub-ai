from __future__ import annotations

from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.core.types import UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext

"""POST_MODEL middleware: builds the recovery content a truncated
(max-output-tokens-capped) LLM response needs, so `Agent.run()` never
executes tool calls parsed from incomplete arguments — see the `truncated`
branch in `agent/__init__.py`'s turn loop.

Pure content construction: whether to actually short-circuit the rest of the
turn (skip tool execution, `continue` to the next turn) stays owned by the
loop, since that is genuine turn-loop control flow, not a policy a
middleware can express by itself.
"""

__all__ = ["default_truncation_recovery"]

_TOOL_CALL_TRUNCATION_NOTE = (
    "[Tool call not executed: your response was cut off at the "
    "maximum output-token limit, so this call's arguments were "
    "incomplete. Retry with a shorter response — for long final "
    "outputs, emit the content across multiple turns or trim it "
    "before calling task_complete.]"
)

_TOOL_CALL_TRUNCATION_ESCALATED = (
    "[Tool call not executed: your response was cut off at the "
    "maximum output-token limit AGAIN. You have already been "
    "truncated multiple times in a row — you MUST change your "
    "approach:\n"
    "- Split your code into SMALLER pieces across multiple "
    "run_code calls (e.g. build part 1, then part 2)\n"
    "- Reduce the amount of content in a single tool call\n"
    "- Write a shorter, simpler version\n"
    "Do NOT retry the same long output — it will be truncated again.]"
)

_TEXT_ONLY_CONTINUATION_NOTE = (
    "[System: your previous response was cut off at the maximum "
    "output-token limit and is incomplete. Continue from where "
    "you stopped, keeping the remainder concise.]"
)

_CONSECUTIVE_TRUNCATION_THRESHOLD = 2


def default_truncation_recovery():
    consecutive_truncations = 0

    async def _middleware(ctx: ModelResponseContext, next_fn) -> None:
        nonlocal consecutive_truncations
        if getattr(ctx.response, "truncated", False):
            consecutive_truncations += 1
            if ctx.tool_calls:
                note = (
                    _TOOL_CALL_TRUNCATION_ESCALATED
                    if consecutive_truncations > _CONSECUTIVE_TRUNCATION_THRESHOLD
                    else _TOOL_CALL_TRUNCATION_NOTE
                )
                ctx.recovery_tool_results = [
                    CoreToolResult(tool_call_id=c.id, name=c.name, content=note, is_error=True)
                    for c in ctx.tool_calls
                ]
            else:
                ctx.recovery_message = UserMessage(content=_TEXT_ONLY_CONTINUATION_NOTE, injected=True)
        else:
            consecutive_truncations = 0
        await next_fn()

    return _middleware
