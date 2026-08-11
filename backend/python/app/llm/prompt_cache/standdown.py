"""Gateway/client stand-down detection.

If a cache marker already exists anywhere in the outgoing payload —
because a LiteLLM proxy has `enable_anthropic_prompt_caching` turned
on, or a caller pre-annotated messages itself — some entity other than
this strategy has already made caching decisions for this request.
Layering more breakpoints on top risks exceeding a provider's hard cap
(Anthropic: 4; OpenAI explicit mode: 4), so the correct response is to
stand down entirely rather than try to reconcile with markers this
module did not place and cannot enumerate the intent of.

Checks BOTH marker shapes this codebase's strategies can produce —
Anthropic/Bedrock's `cache_control` and OpenAI's `prompt_cache_breakpoint`
— regardless of which strategy is asking, since a gateway may have
annotated a payload with either shape before it reached this code.
"""

from __future__ import annotations

from typing import Any

_MARKER_KEYS = ("cache_control", "prompt_cache_breakpoint")


def _block_has_cache_marker(block: Any) -> bool:  # noqa: ANN401
    if not isinstance(block, dict):
        return False
    if any(key in block for key in _MARKER_KEYS):
        return True
    # OpenAI tool shape: {"type": "function", "function": {..., "cache_control": ...}}
    function = block.get("function")
    return isinstance(function, dict) and any(key in function for key in _MARKER_KEYS)


def count_existing_breakpoints(
    messages: list[dict[str, Any]],
    system: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None,
) -> int:
    """Counts every cache marker already present anywhere in the
    payload — messages (including nested content blocks), system
    blocks, and tool schemas."""
    count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            count += sum(1 for block in content if _block_has_cache_marker(block))
        elif _block_has_cache_marker(msg):
            count += 1
    if system:
        count += sum(1 for block in system if _block_has_cache_marker(block))
    if tools:
        count += sum(1 for tool in tools if _block_has_cache_marker(tool))
    return count


def should_stand_down(
    messages: list[dict[str, Any]],
    system: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None,
) -> bool:
    """True when any cache marker already exists anywhere in the
    payload."""
    return count_existing_breakpoints(messages, system, tools) > 0


__all__ = ["count_existing_breakpoints", "should_stand_down"]
