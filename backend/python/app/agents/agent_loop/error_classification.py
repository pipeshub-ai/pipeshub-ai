"""Classifies raw agent/transport error strings into a stable `errorCode`
plus a user-friendly message, so the frontend can render something better
than a raw exception string (e.g. `LLM call failed: LangChain transport
error (complete): Error code: 429 - {...}`) and — for `rate_limit`/
`server_error`/`timeout` — offer a "try again" affordance instead of a dead
end.

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

_RATE_LIMIT_HINTS = ("429", "rate limit", "rate_limit", "too many requests")
_AUTH_HINTS = ("401", "403", "unauthorized", "authentication", "invalid api key", "permission denied")
_SERVER_ERROR_HINTS = ("500", "502", "503", "504", "service unavailable", "bad gateway")
_TIMEOUT_HINTS = ("timeout", "timed out")

_USER_MESSAGES: dict[str, str] = {
    "rate_limit": "The AI service is currently rate limited. Please try again in a moment.",
    "auth_error": "There was an authentication issue with the AI service. Please contact your administrator.",
    "server_error": "The AI service is temporarily unavailable. Please try again shortly.",
    "timeout": "The AI service took too long to respond. Please try again.",
    "unknown": "I encountered an issue while processing your request. Please try again.",
}


def classify_error(error_msg: str) -> tuple[str, str]:
    """Returns `(error_code, user_message)` for a raw error string.

    `error_code` is one of `rate_limit` / `auth_error` / `server_error` /
    `timeout` / `unknown` — checked in this priority order since a single
    message can contain multiple hints (e.g. a 429 body that also mentions
    "too many requests"; rate limit is the most actionable classification
    to surface first).
    """
    lower = error_msg.lower()

    if any(hint in lower for hint in _RATE_LIMIT_HINTS):
        error_code = "rate_limit"
    elif any(hint in lower for hint in _AUTH_HINTS):
        error_code = "auth_error"
    elif any(hint in lower for hint in _SERVER_ERROR_HINTS):
        error_code = "server_error"
    elif any(hint in lower for hint in _TIMEOUT_HINTS):
        error_code = "timeout"
    else:
        error_code = "unknown"

    return error_code, _USER_MESSAGES[error_code]


__all__ = ["classify_error"]
