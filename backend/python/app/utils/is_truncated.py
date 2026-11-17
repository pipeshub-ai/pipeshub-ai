"""Whether a model response was cut off by its output limit, for every provider the indexing models use.

One helper so the retry-at-full-length decision and the cache's "never store a truncated result" rule read
the same fields. A response with no metadata, or a metadata shape not listed, is not truncated: the check
only ever says "cut off" for a provider's documented stop reason.
"""

from collections.abc import Mapping
from typing import Any

#: (metadata field, stop reasons meaning "hit the output limit"), lowercased and stripped of an enum prefix.
#:  * OpenAI, Azure OpenAI, Groq, Mistral and OpenAI-compatible servers: ``finish_reason == "length"``
#:  * Anthropic: ``stop_reason == "max_tokens"``
#:  * Gemini and Vertex: ``finish_reason == "MAX_TOKENS"`` (a string or ``FinishReason.MAX_TOKENS``)
#:  * Bedrock Converse: ``stopReason == "max_tokens"``
#:  * Ollama: ``done_reason == "length"``
_TRUNCATING = {
    "finish_reason": frozenset({"length", "max_tokens", "model_length"}),
    "stop_reason": frozenset({"max_tokens", "model_context_window_exceeded"}),
    "stopreason": frozenset({"max_tokens", "model_context_window_exceeded"}),
    "done_reason": frozenset({"length"}),
}


def _normal(value: object) -> str:
    name = getattr(value, "name", None)
    text = name if isinstance(name, str) else str(value)
    return text.rsplit(".", 1)[-1].strip().lower()


def _metadata(response: object) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    meta = getattr(response, "response_metadata", None)
    return meta if isinstance(meta, Mapping) else {}


def is_truncated(response: object) -> bool:
    meta = _metadata(response)
    for field, reasons in _TRUNCATING.items():
        for key, value in meta.items():
            if isinstance(key, str) and key.lower() == field and value is not None and _normal(value) in reasons:
                return True
    # LangChain keeps a provider's raw stop reason here when it also normalises one.
    extra = getattr(response, "additional_kwargs", None)
    if isinstance(extra, Mapping):
        for field, reasons in _TRUNCATING.items():
            value = extra.get(field)
            if value is not None and _normal(value) in reasons:
                return True
    # OpenAI's Responses API reports a cut-off answer as ``status == "incomplete"`` with the cause under
    # ``incomplete_details.reason``; only the output-token causes count (not ``content_filter``).
    details = meta.get("incomplete_details")
    if isinstance(details, Mapping):
        reason = details.get("reason")
        if reason is not None and _normal(reason) in {"max_output_tokens", "max_tokens"}:
            return True
    return False
