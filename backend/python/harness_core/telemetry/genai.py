"""OpenTelemetry GenAI semantic conventions as the harness uses them (``open-telemetry/semantic-conventions-genai``,
2026-10): attribute names, operation names, provider names, and the opt-in content shapes.

Content (``gen_ai.input.messages``, ``gen_ai.output.messages``, ``gen_ai.tool.call.arguments``,
``gen_ai.tool.call.result``) is written only by a tracer whose ``capture_content`` is set: the conventions say
instrumentations SHOULD NOT capture it by default.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import JsonValue

    from harness_core.kernel.core.responses import TokenUsage

OPERATION = "gen_ai.operation.name"
PROVIDER = "gen_ai.provider.name"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
REQUEST_STREAM = "gen_ai.request.stream"
RESPONSE_ID = "gen_ai.response.id"
INPUT_TOKENS = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
# Parts of ``INPUT_TOKENS`` (the conventions count them inside it, as ``TokenUsage`` does).
CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
CACHE_CREATION_INPUT_TOKENS = "gen_ai.usage.cache_creation.input_tokens"
CONVERSATION_ID = "gen_ai.conversation.id"
AGENT_NAME = "gen_ai.agent.name"
AGENT_ID = "gen_ai.agent.id"
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"
TOOL_TYPE = "gen_ai.tool.type"
TOOL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_RESULT = "gen_ai.tool.call.result"
INPUT_MESSAGES = "gen_ai.input.messages"
OUTPUT_MESSAGES = "gen_ai.output.messages"
ERROR_TYPE = "error.type"

CHAT = "chat"
EXECUTE_TOOL = "execute_tool"
INVOKE_AGENT = "invoke_agent"

CONTENT_ATTRIBUTES = frozenset({INPUT_MESSAGES, OUTPUT_MESSAGES, TOOL_ARGUMENTS, TOOL_RESULT})

# The conventions' well-known provider names, from the harness's provider kinds; others pass through as named.
_PROVIDERS = {
    "openai": "openai",
    "openaicompatible": "openai",
    "azureopenai": "azure.ai.openai",
    "azure": "azure.ai.openai",
    "anthropic": "anthropic",
    "bedrock": "aws.bedrock",
    "vertexai": "gcp.vertex_ai",
    "gemini": "gcp.gemini",
    "mistral": "mistral_ai",
    "cohere": "cohere",
}


def provider_name(provider: str) -> str:
    return _PROVIDERS.get(provider.lower().replace("_", "").replace("-", ""), provider)


def usage_attributes(usage: TokenUsage | None) -> dict[str, int | None]:
    """A model call's usage as span attributes; every value ``None`` (so not set) when the provider reported none."""
    if usage is None:
        return dict.fromkeys((INPUT_TOKENS, OUTPUT_TOKENS, CACHE_READ_INPUT_TOKENS, CACHE_CREATION_INPUT_TOKENS))
    return {
        INPUT_TOKENS: usage.input_tokens,
        OUTPUT_TOKENS: usage.output_tokens,
        CACHE_READ_INPUT_TOKENS: usage.cache_read_tokens,
        CACHE_CREATION_INPUT_TOKENS: usage.cache_write_tokens,
    }


def text_messages(messages: Iterable[tuple[str, str]]) -> str:
    """``gen_ai.input.messages`` / ``gen_ai.output.messages``: ``[{role, parts: [{type: text, content}]}]`` as JSON."""
    shaped: list[JsonValue] = [
        {"role": role, "parts": [{"type": "text", "content": text}]} for role, text in messages
    ]
    return json.dumps(shaped, ensure_ascii=False)


__all__ = [
    "AGENT_ID",
    "AGENT_NAME",
    "CACHE_CREATION_INPUT_TOKENS",
    "CACHE_READ_INPUT_TOKENS",
    "CHAT",
    "CONTENT_ATTRIBUTES",
    "CONVERSATION_ID",
    "ERROR_TYPE",
    "EXECUTE_TOOL",
    "INPUT_MESSAGES",
    "INPUT_TOKENS",
    "INVOKE_AGENT",
    "OPERATION",
    "OUTPUT_MESSAGES",
    "OUTPUT_TOKENS",
    "PROVIDER",
    "REQUEST_MODEL",
    "REQUEST_STREAM",
    "RESPONSE_ID",
    "RESPONSE_MODEL",
    "TOOL_ARGUMENTS",
    "TOOL_CALL_ID",
    "TOOL_NAME",
    "TOOL_RESULT",
    "TOOL_TYPE",
    "provider_name",
    "text_messages",
    "usage_attributes",
]
