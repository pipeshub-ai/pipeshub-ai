"""Classifies raw agent/transport error strings into a stable `errorCode`
plus a user-friendly message, so the frontend can render something better
than a raw exception string (e.g. `LLM call failed: LangChain transport
error (complete): Error code: 429 - {...}`) and — for `rate_limit`/
`server_error`/`timeout` — offer a "try again" affordance instead of a dead
end.

`invalid_request` (provider 400) and `request_too_large` (413 / context-
length exceeded) are exceptions to the "never leak provider text" rule:
retrying the same request cannot succeed, so the provider's own
`error.message` is the only useful thing to show (e.g. Groq's
`invalid Qwen3.8 reasoning_effort` or its token-limit detail).
Rate-limit / auth / 5xx bodies still stay canned.

Shared by `RespondPipeline._emit_error_response` (agent run completed with
`success=False`) and `stream_bridge.py`'s top-level exception handler
(anything that blew up before/around the agent run itself), so both error
surfaces produce the same `errorCode` vocabulary for a given underlying
failure.

Deliberately string-matching rather than inspecting exception types: by the
time an error reaches these call sites it has already been flattened to
`AgentResult.error` (a `str`) by `Agent.fail()`, so the original exception
object/type is no longer available.
"""

from __future__ import annotations

import ast
import json
import re

# Provider content-moderation rejections (Azure `content_filter` /
# `ResponsibleAIPolicyViolation`, OpenAI `content_policy_violation`, ...).
# Checked FIRST: these bodies routinely also contain words that match the
# broader hint lists below, and unlike every other class this one is
# deterministic — retrying the identical prompt always fails again, so
# telling the user "please try again" (the rate-limit/server-error advice)
# would be actively wrong.
_CONTENT_FILTER_HINTS = (
    "content_filter",
    "content management policy",
    "content policy",
    "content_policy",
    "responsibleaipolicyviolation",
    "content filtering polic",
)
# Checked BEFORE rate_limit: Groq (and others) return 413 with
# `code: rate_limit_exceeded` and "rate limit" in the body, but the request
# is permanently too large — "try again in a moment" is wrong advice.
_REQUEST_TOO_LARGE_HINTS = (
    "request too large",
    "payload too large",
    "error code: 413",
    "reduce your message size",
    "context length exceeded",
    "maximum context length",
)
_RATE_LIMIT_HINTS = ("429", "rate limit", "rate_limit", "too many requests")
_AUTH_HINTS = ("401", "403", "unauthorized", "authentication", "invalid api key", "permission denied")
# Matched after content_filter: Azure prompt-shields are also HTTP 400.
_INVALID_REQUEST_HINTS = ("invalid_request_error", "error code: 400")
_SERVER_ERROR_HINTS = ("500", "502", "503", "504", "service unavailable", "bad gateway")
_TIMEOUT_HINTS = ("timeout", "timed out")

_USER_MESSAGES: dict[str, str] = {
    "content_filter": (
        "The AI provider's content filter blocked this request. This is often a "
        "false positive triggered by something in the conversation or in retrieved "
        "documents rather than by your question itself. Please rephrase your "
        "question, or start a new conversation and try again."
    ),
    "rate_limit": "The AI service is currently rate limited. Please try again in a moment.",
    "auth_error": "There was an authentication issue with the AI service. Please contact your administrator.",
    "request_too_large": (
        "The request exceeds the model's token limit. Please shorten your message "
        "or start a new conversation to reduce context size."
    ),
    "invalid_request": (
        "The AI service rejected this request. Please check the model configuration "
        "and try again."
    ),
    "server_error": "The AI service is temporarily unavailable. Please try again shortly.",
    "timeout": "The AI service took too long to respond. Please try again.",
    "unknown": "I encountered an issue while processing your request. Please try again.",
}

_ERROR_PAYLOAD_RE = re.compile(
    r"Error code:\s*\d+\s*-\s*(\{.*\})\s*$",
    re.DOTALL | re.IGNORECASE,
)
_MAX_PROVIDER_MESSAGE_LEN = 240


def _sanitize_provider_message(message: str) -> str | None:
    text = " ".join(message.split())
    if not text or "traceback" in text.lower():
        return None
    if len(text) > _MAX_PROVIDER_MESSAGE_LEN:
        text = text[: _MAX_PROVIDER_MESSAGE_LEN - 1] + "…"
    return text


def _extract_provider_message(error_msg: str) -> str | None:
    """Pull the provider's `error.message` out of an OpenAI-shaped error body.

    LangChain wraps these as ``Error code: NNN - {'error': {'message': '...', ...}}``
    (Python dict repr) or the JSON equivalent. Returns ``None`` when nothing
    extractable is present so callers can fall back to the canned string.
    """
    payload_match = _ERROR_PAYLOAD_RE.search(error_msg.strip())
    parsed: object = None
    if payload_match:
        raw = payload_match.group(1)
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
    if isinstance(parsed, dict):
        err = parsed.get("error")
        candidate = None
        if isinstance(err, dict):
            candidate = err.get("message")
        elif isinstance(err, str):
            candidate = err
        if not candidate:
            candidate = parsed.get("message")
        if isinstance(candidate, str) and candidate.strip():
            return _sanitize_provider_message(candidate)
    return None


def classify_error(error_msg: str) -> tuple[str, str]:
    """Returns `(error_code, user_message)` for a raw error string.

    `error_code` is one of `content_filter` / `request_too_large` /
    `rate_limit` / `auth_error` / `invalid_request` / `server_error` /
    `timeout` / `unknown` — checked in this priority order since a single
    message can contain multiple hints (e.g. a Groq 413 body that also
    mentions "rate_limit_exceeded"; the request-too-large classification
    must win because "try again in a moment" is wrong advice for a
    permanently oversized request).
    """
    lower = error_msg.lower()

    if any(hint in lower for hint in _CONTENT_FILTER_HINTS):
        error_code = "content_filter"
    elif any(hint in lower for hint in _REQUEST_TOO_LARGE_HINTS):
        error_code = "request_too_large"
    elif any(hint in lower for hint in _RATE_LIMIT_HINTS):
        error_code = "rate_limit"
    elif any(hint in lower for hint in _AUTH_HINTS):
        error_code = "auth_error"
    elif any(hint in lower for hint in _INVALID_REQUEST_HINTS):
        error_code = "invalid_request"
    elif any(hint in lower for hint in _SERVER_ERROR_HINTS):
        error_code = "server_error"
    elif any(hint in lower for hint in _TIMEOUT_HINTS):
        error_code = "timeout"
    else:
        error_code = "unknown"

    if error_code in ("invalid_request", "request_too_large"):
        provider_msg = _extract_provider_message(error_msg)
        if provider_msg:
            prefix = _USER_MESSAGES[error_code].split(".")[0]
            return error_code, f"{prefix}: {provider_msg}"

    return error_code, _USER_MESSAGES[error_code]


__all__ = ["classify_error"]
