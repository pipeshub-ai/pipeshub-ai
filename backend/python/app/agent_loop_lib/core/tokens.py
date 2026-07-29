from __future__ import annotations

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
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


def extract_text(message: Message) -> str:
    """Best-effort plain-text extraction from a message's content, ignoring
    image parts. Shared by shapers that need to summarize or preview a
    message rather than count its tokens."""
    match message:
        case SystemMessage() | ToolMessage():
            return message.content or ""
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


def count_message_tokens(message: Message) -> int:
    """Estimate tokens for a single message (content + tool_calls + overhead)."""
    text = extract_text(message)
    total_chars = len(text)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            total_chars += len(tc.name) + len(str(tc.arguments))
    return _MESSAGE_OVERHEAD_TOKENS + (total_chars // _CHARS_PER_TOKEN)


def count_tokens(messages: list[Message]) -> int:
    return sum(count_message_tokens(m) for m in messages)


def count_text_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN
