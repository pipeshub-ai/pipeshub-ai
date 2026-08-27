from __future__ import annotations

import math

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    Message,
    SystemMessage,
    TextPart,
    ThinkingPart,
    ToolMessage,
    UserMessage,
)

"""Single source of truth for pre-call token estimation.

This is deliberately a fast heuristic, not a real tokenizer: it runs on
every turn, for every shaper, before the provider tells us the truth via
`TokenUsage` (see modules/providers/budget/tracker.py, which uses REAL usage for cost/limit
enforcement). This module only needs to be good enough to make shaping
decisions (should we truncate? evict? compact?) — being off by ~10-20% here
has zero cost impact since it never feeds the budget ledger.
"""

_CHARS_PER_TOKEN = 4
# Every message costs a few tokens of protocol overhead (role marker,
# separators) regardless of content, on top of raw character count.
_MESSAGE_OVERHEAD_TOKENS = 4

# Images used to count as zero here, which is how a request could carry tens
# of thousands of visual tokens while every shaper believed the context was
# nearly empty and compaction never fired. Providers tile images at roughly
# this granularity (Anthropic 28x28 visual tokens, OpenAI 32x32 patches,
# Gemini 768px tiles at a flat rate), so one grid is close enough for a
# heuristic whose job is only to decide when to compact.
_IMAGE_PATCH_PX = 28
# Ceiling per image, matching the point where providers stop charging more
# because they have downscaled the image to their native raster.
_MAX_IMAGE_TOKENS = 1_600
# What an unmeasured image is assumed to cost. Deliberately near the ceiling:
# under-counting is the failure that broke compaction, over-counting only
# makes it slightly eager.
_UNKNOWN_IMAGE_TOKENS = 1_500


def extract_text(message: Message) -> str:
    """Best-effort plain-text extraction from a message's content, ignoring
    image parts. Shared by shapers that need to summarize or preview a
    message rather than count its tokens."""
    match message:
        case SystemMessage():
            return message.content or ""
        case ToolMessage():
            return message.text
        case UserMessage():
            if isinstance(message.content, str):
                return message.content
            return " ".join(
                part.text for part in message.content if isinstance(part, TextPart)
            )
        case AssistantMessage():
            parts: list[str] = []
            for part in message.content:
                if isinstance(part, TextPart):
                    parts.append(part.text)
                elif isinstance(part, ThinkingPart):
                    parts.append(part.thinking)
            return " ".join(parts)
        case _:
            return ""


def count_image_tokens(part: ImagePart) -> int:
    """Visual-token cost of one image, from its dimensions when known."""
    if not part.width or not part.height:
        return _UNKNOWN_IMAGE_TOKENS
    patches = (
        math.ceil(part.width / _IMAGE_PATCH_PX)
        * math.ceil(part.height / _IMAGE_PATCH_PX)
    )
    return min(patches, _MAX_IMAGE_TOKENS)


def count_message_tokens(message: Message) -> int:
    """Estimate tokens for a single message (content + images + tool_calls +
    overhead)."""
    text = extract_text(message)
    total_chars = len(text)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            total_chars += len(tc.name) + len(str(tc.arguments))
    content = getattr(message, "content", None)
    image_tokens = (
        sum(count_image_tokens(p) for p in content if isinstance(p, ImagePart))
        if isinstance(content, list) else 0
    )
    return _MESSAGE_OVERHEAD_TOKENS + (total_chars // _CHARS_PER_TOKEN) + image_tokens


def count_tokens(messages: list[Message]) -> int:
    return sum(count_message_tokens(m) for m in messages)


def count_text_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN
