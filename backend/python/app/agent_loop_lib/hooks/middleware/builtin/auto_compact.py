from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agent_loop_lib.core.tokens import count_tokens
from app.agent_loop_lib.core.types import Message, UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

Summarizer = Callable[[list[Message]], Awaitable[str]]


def _naive_summary(messages: list[Message]) -> str:
    """Fallback used when no LLM summarizer is configured: a compact,
    lossy joining of message text so compaction still bounds tokens even
    without a model call available (e.g. in tests, or transport-less runs)."""
    from app.agent_loop_lib.core.tokens import extract_text

    parts = []
    for m in messages:
        text = extract_text(m).strip()
        if text:
            parts.append(f"[{m.role.value}] {text[:200]}")
    return " | ".join(parts)[:2_000] or "(no content)"


def shape_auto_compact(
    summarizer: Summarizer | None = None,
    trigger_ratio: float = 0.85,
    keep_last_n_messages: int = 6,
    pin_first_n: int = 1,
):
    """Layer 5 (most expensive, last-resort) context shaper: once messages
    exceed `trigger_ratio * budget.max_tokens` even after the cheaper layers
    ran, summarize everything except the pinned head and the most recent
    `keep_last_n_messages`, and replace it with one synthetic summary
    message.

    `summarizer` defaults to a naive local join (no model call) so this
    shaper degrades gracefully without a transport; pass an async callable
    that calls the real LLM for production use — `ControlPlane` wires this
    using the already-lazy `TransportRegistry.resolve`, so no extra model
    call happens unless compaction actually triggers. Direct replacement
    for `AutoCompactHook`.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        messages = ctx.messages
        if count_tokens(messages) <= ctx.budget.max_tokens * trigger_ratio:
            await next_fn()
            return
        if len(messages) <= pin_first_n + keep_last_n_messages:
            await next_fn()
            return

        head = messages[:pin_first_n]
        tail = messages[-keep_last_n_messages:] if keep_last_n_messages > 0 else []
        middle = messages[pin_first_n: len(messages) - keep_last_n_messages]
        if not middle:
            await next_fn()
            return

        summary_text = (
            await summarizer(middle) if summarizer is not None else _naive_summary(middle)
        )
        summary_msg = UserMessage(
            content=f"[Auto-compacted summary of {len(middle)} earlier message(s)]\n{summary_text}",
        )
        ctx.messages = [*head, summary_msg, *tail]
        await next_fn()

    return _middleware
