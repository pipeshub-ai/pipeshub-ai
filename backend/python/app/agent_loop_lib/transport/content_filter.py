"""A provider's content filter stopping a response it had already accepted.

A filter that rejects the request outright arrives as an HTTP 400 and is
classified from the error text. One that stops the response instead ends the
stream normally -- `finish_reason="content_filter"` on Chat Completions,
`incomplete_details.reason="content_filter"` on the Responses API, Anthropic's
`stop_reason="refusal"`, Gemini's `finish_reason="SAFETY"` or a
`prompt_feedback.block_reason` with no candidates -- carrying no output and no
usage. Treated as an ordinary end of turn, that empty reply was nudged, retried
and finally shown as a canned refusal. Raising instead routes it to the same
`content_filter` error the 400 gets.
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.exceptions import TransportError

__all__ = [
    "CONTENT_FILTER_STOP_REASONS",
    "GEMINI_FILTER_FINISH_REASONS",
    "content_filter_error",
    "gemini_prompt_block_reason",
]

GEMINI_FILTER_FINISH_REASONS = frozenset({
    "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY",
})

CONTENT_FILTER_STOP_REASONS = frozenset({"content_filter", "refusal"}) | GEMINI_FILTER_FINISH_REASONS

_UNSET_BLOCK_REASONS = frozenset({"", "0", "BLOCKED_REASON_UNSPECIFIED", "BLOCK_REASON_UNSPECIFIED"})


def gemini_prompt_block_reason(prompt_feedback: Any) -> str | None:
    """Why Gemini blocked the prompt, or None when it did not.

    Takes the SDK object or the dict LangChain copies into response metadata;
    the reason may be an enum, its name, or the proto's int (0 is unset).
    """
    if isinstance(prompt_feedback, dict):
        raw = prompt_feedback.get("block_reason")
    else:
        raw = getattr(prompt_feedback, "block_reason", None)
    if raw is None:
        return None
    name = str(getattr(raw, "name", None) or raw)
    return None if name in _UNSET_BLOCK_REASONS else name


def content_filter_error(context: str, detail: str) -> TransportError:
    """Non-retryable: the same request is filtered the same way again."""
    return TransportError(
        f"Provider content_filter stopped the response ({context}): {detail}",
        retryable=False,
    )
