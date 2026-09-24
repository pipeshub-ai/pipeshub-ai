"""Provider/gateway error signatures that a single retry can recover from.

Shared by every transport so the LangChain and direct-SDK paths cannot drift on
what counts as recoverable. Matched case-insensitively against ``str(exc)`` --
deliberately loose rather than a status-code check, because these come from very
different SDKs (openai, httpx-based gateway clients, ...) with no shared
exception hierarchy.
"""

from __future__ import annotations

# Emitted when reasoning + bound function tools cannot both go through the API
# shape the request used.
# - "please use /v1/responses instead": Chat Completions rejected the
#   combination.
# - "please use /v1/chat/completions instead": the inverse -- a Responses-API
#   attempt landed on a backend that only supports this on Chat Completions.
# - "tool_choice.function": Chat-Completions-shaped tool_choice sent to a
#   Responses-only model/deployment.
API_SHAPE_CONFLICT_MARKERS = (
    "please use /v1/responses instead",
    "please use /v1/chat/completions instead",
    "tool_choice.function",
)

# Emitted when a provider/gateway refuses to let reasoning be turned off at all
# -- observed on some OpenRouter-proxied Gemini models: "Reasoning is mandatory
# for this endpoint and cannot be disabled."
REASONING_MANDATORY_CONFLICT_MARKERS = ("reasoning is mandatory",)

# Emitted when a provider accepts image blocks only on ``role: "user"`` messages
# and the request carried one inside a tool result. OpenAI/Azure Chat
# Completions: "Invalid 'messages[5]'. Image URLs are only allowed for messages
# with role 'user', but this message with role 'tool' contains an image URL."
# Recovering means moving those images into a user message, not changing the
# model's configuration -- see `LangChainTransport._tool_image_fallback`.
TOOL_RESULT_IMAGE_MARKERS = (
    "only allowed for messages with role 'user'",
    'only allowed for messages with role "user"',
)


def is_api_shape_conflict(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in API_SHAPE_CONFLICT_MARKERS)


def is_reasoning_mandatory_conflict(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in REASONING_MANDATORY_CONFLICT_MARKERS)


def is_tool_result_image_conflict(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in TOOL_RESULT_IMAGE_MARKERS)


def wants_responses_api(exc: Exception) -> bool:
    """True when the error asks for the Responses API specifically.

    The marker names the endpoint the provider wants, so the direct-SDK
    transport can pick a shape from the error rather than blindly toggling.
    """
    message = str(exc).lower()
    return "please use /v1/responses instead" in message or "tool_choice.function" in message
