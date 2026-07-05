from __future__ import annotations

from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

_CLEARED_PLACEHOLDER = "[cleared — stale tool result, call the tool again if you need this data]"


def shape_tool_result_clearing(keep_last_n_turns: int = 3, trigger_ratio: float = 0.5):
    """Layer 2 context shaper: replaces stale TOOL message payloads with a
    short placeholder once they age past `keep_last_n_turns`.

    Only clears when the running total is over `budget.max_tokens *
    trigger_ratio` — recent, still-relevant results are left alone even if
    old ones exist, and nothing is cleared at all while comfortably under
    budget. The corresponding `tool_use` block (in the ASSISTANT message)
    is never touched, so tool_use/tool_result pairing stays valid for
    providers that require it. Direct replacement for `ToolResultClearingHook`.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        from app.agent_loop_lib.core.tokens import count_tokens

        messages = ctx.messages
        if count_tokens(messages) <= ctx.budget.max_tokens * trigger_ratio:
            await next_fn()
            return

        tool_indices = [i for i, m in enumerate(messages) if m.role == MessageRole.TOOL]
        if len(tool_indices) <= keep_last_n_turns:
            await next_fn()
            return
        clearable = set(tool_indices[: len(tool_indices) - keep_last_n_turns])

        shaped = []
        for i, msg in enumerate(messages):
            if i in clearable and isinstance(msg.content, str) and msg.content != _CLEARED_PLACEHOLDER:
                shaped.append(msg.model_copy(update={"content": _CLEARED_PLACEHOLDER}))
            else:
                shaped.append(msg)
        ctx.messages = shaped
        await next_fn()

    return _middleware
