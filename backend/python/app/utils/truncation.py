"""Whether a model's answer was cut off at its output-token cap, whatever the provider calls it.

A cut-off answer is not a failure the provider can be blamed for, and sending the same request again usually gets the
same cut. Callers that pay per call use this to decide, once, what to do with a truncated answer instead of retrying it
like an error.

Shapes (LangChain ``response_metadata``): OpenAI, Groq, Mistral, xAI, Together, Fireworks and every OpenAI-protocol
server ``finish_reason == "length"``; OpenAI's Responses API ``status == "incomplete"`` with
``incomplete_details.reason == "max_output_tokens"``; Anthropic ``stop_reason == "max_tokens"``; Gemini and Vertex
``finish_reason == "MAX_TOKENS"``; Bedrock Converse ``stopReason == "max_tokens"``; Ollama ``done_reason == "length"``;
Cohere ``finish_reason == "MAX_TOKENS"``.
"""

from __future__ import annotations

from typing import Any

_CUT_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})
_REASON_KEYS = ("finish_reason", "stop_reason", "stopReason", "done_reason")


def _normalise(value: object) -> str:
    # An enum or its repr ("FinishReason.MAX_TOKENS") names the reason after the last dot.
    text = getattr(value, "name", None) or str(value)
    return text.rsplit(".", 1)[-1].strip().lower()


def is_truncated(message: Any) -> bool:  # noqa: ANN401 - a LangChain message, or anything with ``response_metadata``
    """True when ``message`` ended because the output-token cap was reached."""
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return False
    for key in _REASON_KEYS:
        reason = metadata.get(key)
        if reason is not None and _normalise(reason) in _CUT_REASONS:
            return True
    details = metadata.get("incomplete_details")
    if isinstance(details, dict):
        reason = details.get("reason")
        return reason is not None and _normalise(reason) in _CUT_REASONS
    return False
