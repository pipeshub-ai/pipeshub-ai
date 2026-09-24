from __future__ import annotations

from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

_MARKER = "\n[…truncated"
_OWN_SUFFIX = "by budget_reduction]"


def _marker(dropped: int) -> str:
    return f"{_MARKER} {dropped} chars {_OWN_SUFFIX}\n"


def _cap_parts(msg, max_result_chars: int, truncate):
    """Cap the text parts of a multipart tool message, leaving images alone.

    Images are bounded by their own admission path; what runs away here is the
    text a large record renders to.
    """
    total = sum(
        len(p.text) for p in msg.content
        if getattr(p, "type", None) == "text" and isinstance(getattr(p, "text", None), str)
    )
    if total <= max_result_chars or _OWN_SUFFIX in str(
        next((p.text for p in msg.content if getattr(p, "type", None) == "text"), "")
    ):
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
    return msg.model_copy(update={"content": capped})


def shape_budget_reduction(max_result_chars: int = 64_000):
    """Layer 1 (cheapest) context shaper: caps every individual TOOL message's
    content at `max_result_chars`.

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

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        shaped = []
        for msg in ctx.messages:
            if msg.role != MessageRole.TOOL:
                shaped.append(msg)
                continue
            # Multipart content (a tool that returned images) used to skip this
            # cap entirely -- the one result shape most likely to be oversized.
            if isinstance(msg.content, list):
                shaped.append(_cap_parts(msg, max_result_chars, _truncate))
                continue
            if not isinstance(msg.content, str):
                shaped.append(msg)
                continue
            # Artifact-bearing messages have their full content safely stored
            # in the artifact store — truncate here like any other tool
            # result.  L2 artifact_compaction handles turn-aware replacement
            # with compact references on later turns; the model can call
            # retrieve_artifact_content for the full data.
            if len(msg.content) <= max_result_chars:
                shaped.append(msg)
                continue
            # Skip only messages already truncated by THIS shaper (avoid
            # double-truncation on re-runs).  Foreign truncation markers
            # (e.g. from retrieve_artifact_content) do NOT grant a pass —
            # those messages can still be far over max_result_chars.
            if _OWN_SUFFIX in msg.content:
                shaped.append(msg)
                continue
            shaped.append(msg.model_copy(update={"content": _truncate(msg.content)}))
        ctx.messages = shaped
        await next_fn()

    return _middleware
