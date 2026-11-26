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
import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.model_identity.errors import classify_for_user
from app.utils.log_redaction import URL_PATTERN, redact_secrets_in_text

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Provider content-moderation rejections (Azure `content_filter` /
# `ResponsibleAIPolicyViolation`, OpenAI `content_policy_violation`, ...).
# Checked FIRST: these bodies routinely also contain words that match the
# broader hint lists below, and unlike every other class this one is
# deterministic — retrying the identical prompt always fails again, so
# telling the user "please try again" (the rate-limit/server-error advice)
# would be actively wrong.
_CONTENT_FILTER_HINTS = (
    "content_filter",
    # The SDKs' wording for a filtered finish (langchain-openai's Azure model,
    # openai's ContentFilterFinishReasonError); anchored, so a bare mention of
    # "content filter" elsewhere in an error is not a refusal.
    "due to a content filter",
    "rejected by the content filter",
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
    "context_length_exceeded",
    "maximum context length",
    # Azure OpenAI: "Your input exceeds the context window of this model."
    "exceeds the context window",
    # `Agent.step` ends the run with this when the shapers cannot fit the
    # context into the model's window (see agent_loop_lib/context/errors.py).
    "context could not be shaped",
)
# Checked BEFORE rate_limit: OpenAI reports a used-up balance as a 429 too, but
# waiting a minute never fixes it.
_QUOTA_HINTS = (
    "insufficient_quota",
    "credit_balance",
    "exceeded your current quota",
    "credit balance is too low",
)
_RATE_LIMIT_HINTS = ("429", "rate limit", "rate_limit", "too many requests")
_AUTH_HINTS = ("401", "403", "unauthorized", "authentication", "invalid api key", "permission denied")
# OpenAI `model_not_found`, Azure `DeploymentNotFound`: the configured model name is wrong.
_MODEL_NOT_FOUND_HINTS = ("error code: 404", "model_not_found", "deploymentnotfound")
# Matched after content_filter: Azure prompt-shields are also HTTP 400.
_INVALID_REQUEST_HINTS = ("invalid_request_error", "error code: 400")
_SERVER_ERROR_HINTS = (
    "500",
    "502",
    "503",
    "504",
    "service unavailable",
    "bad gateway",
    "connection error",
    "connection refused",
    "apiconnectionerror",
    # A failure inside an open stream arrives without its HTTP status, only the
    # provider's wording (Azure OpenAI 503 / 500, Anthropic overloaded_error).
    "high demand",
    "server had an error",
    "server_error",
    "service_unavailable",
    "overloaded",
)
_TIMEOUT_HINTS = ("timeout", "timed out")
_RECONCILIATION_HINT = "needs_reconciliation:"

_USER_MESSAGES: dict[str, str] = {
    "content_filter": (
        "The AI provider's content filter blocked this request. This is often a "
        "false positive triggered by something in the conversation or in retrieved "
        "documents rather than by your question itself. Please rephrase your "
        "question, or start a new conversation and try again."
    ),
    "quota_exceeded": (
        "The AI model's account has run out of credit or reached its usage limit. "
        "Ask a workspace admin to top it up, or to pick another model in "
        "Workspace → AI Models."
    ),
    "rate_limit": (
        "The AI model's provider is limiting requests right now. "
        "Wait a minute, then try again."
    ),
    "auth_error": (
        "The AI model's provider rejected its API key. Ask a workspace admin to "
        "check the key in Workspace → AI Models."
    ),
    "model_not_found": (
        "The AI model's provider couldn't find this model at its endpoint. Ask a "
        "workspace admin to check the model name and endpoint in Workspace → AI Models."
    ),
    "request_too_large": (
        "This conversation is too long for the selected model. Shorten your "
        "message or start a new conversation."
    ),
    "invalid_request": (
        "The AI model rejected this request. Ask a workspace admin to check the "
        "model settings in Workspace → AI Models."
    ),
    "server_error": (
        "The AI model's provider isn't responding right now. "
        "Please try again in a minute."
    ),
    "timeout": "The AI model took too long to answer. Please try again.",
    "unknown": (
        "Something went wrong while answering. Please try again, and if it keeps "
        "happening, contact your workspace admin."
    ),
    "needs_reconciliation": (
        "A tool action may have completed before its result was recorded. "
        "An administrator must reconcile the action before this run can continue."
    ),
    # The run's own budget stopped it (P6.5, LC2FIX): the model call was never made, so no provider failed.
    "budget_exhausted": "This reply stopped because it reached its spending limit.",
    "budget_insufficient": (
        "This reply stopped because its spending limit had too little left for the next step."
    ),
}


def register_answer_error(code: str, message: str) -> None:
    """An error answer code an edition adds (its overlay of ``AnswerErrorCode``) with the words it answers in, for a
    model call that fails with it (``model_call_failure_message``). A code this build has is refused."""
    if code in _USER_MESSAGES and _USER_MESSAGES[code] != message:
        raise ValueError(f"error answer {code!r} is already defined")
    _USER_MESSAGES[code] = message

# A run whose org's MCP servers could not be listed (CPD-7 FB-3): the web app words the code (chatStream.runError).
MCP_UNAVAILABLE = "mcp_unavailable"
_MCP_UNAVAILABLE_MESSAGE = (
    "MCP servers can't be reached right now. Try again in a minute; if it keeps happening, ask your admin."
)

# An unclassified failure of the model call itself still tells the user more
# than "something went wrong": the model call, not the search, failed.
_MODEL_CALL_FAILURE_MARKERS = ("llm call failed", "transport error")
_MODEL_CALL_FAILED_MESSAGE = (
    "The call to the AI model failed while answering. Please try "
    "again, and if it keeps happening, ask a workspace admin to check the "
    "model in Workspace → AI Models."
)

# Checked before the built-in hints. Each returns `(error_code, user_message)`
# or `None` to pass.
_extra_classifiers: list[Callable[[str], tuple[str, str] | None]] = []


def register_error_classifier(fn: Callable[[str], tuple[str, str] | None]) -> None:
    if fn not in _extra_classifiers:
        _extra_classifiers.append(fn)


_ERROR_PAYLOAD_RE = re.compile(
    r"Error code:\s*\d+\s*-\s*(\{.*\})\s*$",
    re.DOTALL | re.IGNORECASE,
)
_MAX_PROVIDER_MESSAGE_LEN = 240
# Provider account metadata that has no business in front of an end user:
# OpenAI-style organization/project ids ("in organization org-abc123 on
# tokens per min") and the account/billing/docs links providers append.
_PROVIDER_ACCOUNT_ID_RE = re.compile(r"\b(?:org|proj|acct|account)[-_][A-Za-z0-9_-]{4,}\b")


def _sanitize_provider_message(message: str) -> str | None:
    text = redact_secrets_in_text(URL_PATTERN.sub("", message))
    text = _PROVIDER_ACCOUNT_ID_RE.sub("[redacted]", text)
    text = " ".join(text.split())
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
    `quota_exceeded` / `rate_limit` / `auth_error` / `model_not_found` / `invalid_request` / `server_error` /
    `timeout` / `unknown` — checked in this priority order since a single
    message can contain multiple hints (e.g. a Groq 413 body that also
    mentions "rate_limit_exceeded"; the request-too-large classification
    must win because "try again in a moment" is wrong advice for a
    permanently oversized request).
    """
    for fn in _extra_classifiers:
        try:
            hit = fn(error_msg)
        except Exception:
            logger.exception("Error classifier %r failed; skipping it", fn)
            continue
        if hit is not None:
            return hit

    # A gateway that enforces per-user budgets and model access answers with the
    # statuses broken providers use; read before the generic 429/403 hints, whose
    # advice ("wait a minute", "the API key is wrong") would be wrong for these.
    gateway = classify_for_user(error_msg)
    if gateway is not None:
        return gateway

    lower = error_msg.lower()

    if _RECONCILIATION_HINT in lower:
        error_code = "needs_reconciliation"
    elif any(hint in lower for hint in _CONTENT_FILTER_HINTS):
        error_code = "content_filter"
    elif any(hint in lower for hint in _REQUEST_TOO_LARGE_HINTS):
        error_code = "request_too_large"
    elif any(hint in lower for hint in _QUOTA_HINTS):
        error_code = "quota_exceeded"
    elif any(hint in lower for hint in _RATE_LIMIT_HINTS):
        error_code = "rate_limit"
    elif any(hint in lower for hint in _AUTH_HINTS):
        error_code = "auth_error"
    elif any(hint in lower for hint in _MODEL_NOT_FOUND_HINTS):
        error_code = "model_not_found"
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

    if error_code == "unknown" and any(marker in lower for marker in _MODEL_CALL_FAILURE_MARKERS):
        return error_code, _MODEL_CALL_FAILED_MESSAGE

    return error_code, _USER_MESSAGES[error_code]


def _status_phrase(status_code: int) -> str | None:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return None


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """`classify_error` for an exception that may already carry a user-facing message.

    A missing model (`LLMNotConfiguredError`) and a deliberate `HTTPException`
    already say what to do; string-matching them would bury that text under a
    canned message (an HTTPException's `str()` even starts with its status code).
    """
    from starlette.exceptions import HTTPException

    from app.agents.mcp.service import MCPInstancesUnavailable
    from app.utils.llm import (  # heavy module; avoid import at load
        LLMNotConfiguredError,
        ModelNotAllowedError,
    )

    if isinstance(exc, ModelNotAllowedError) and exc.answer_code is not None:
        # The rule that refused the model names its own answer; its message says so and what to do.
        return exc.answer_code, str(exc)
    if isinstance(exc, LLMNotConfiguredError):
        return "llm_not_configured", str(exc)
    if isinstance(exc, MCPInstancesUnavailable):
        return MCP_UNAVAILABLE, _MCP_UNAVAILABLE_MESSAGE
    if isinstance(exc, PermissionVerificationUnavailableError):
        # Not "no access": the graph could not say, so the reader is told to retry.
        from app.modules.retrieval.retrieval_service import (
            PERMISSION_CHECK_UNAVAILABLE_MESSAGE,
        )

        return "permission_check_unavailable", PERMISSION_CHECK_UNAVAILABLE_MESSAGE
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            detail = detail.get("message")
        # With no detail, starlette fills in the bare status phrase ("Internal Server Error").
        if isinstance(detail, str) and detail.strip() and detail != _status_phrase(exc.status_code):
            return "request_failed", detail
    return classify_error(str(exc))


def model_call_failure_message(code: str) -> str:
    """The answer for a failed model call already classified as ``code`` (the durable model port journals the
    class, not the provider's text): what ``classify_error`` answers for that call's error."""
    return _MODEL_CALL_FAILED_MESSAGE if code == "unknown" else _USER_MESSAGES[code]


# A provider's error type or code in an error body (OpenAI/Azure `server_error`, Anthropic `overloaded_error`/
# `api_error`) for a failure on its side.
_PROVIDER_SERVER_ERROR_TYPES = frozenset(
    {"server_error", "api_error", "internal_error", "internal_server_error", "overloaded_error", "service_unavailable"}
)
# Network failures by class name, for SDKs that do not subclass OSError or httpx's types (FIX-R2 B4).
_NETWORK_ERROR_NAME_HINTS = (
    "connectionerror", "protocolerror", "readerror", "writeerror", "networkerror", "incompleteread",
)


def _body_fields(exc: BaseException) -> tuple[str, ...]:
    values: list[object] = [getattr(exc, "type", None), getattr(exc, "code", None)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        values += [body.get("type"), body.get("code")]
    return tuple(v.lower() for v in values if isinstance(v, str))


def _class_of(exc: BaseException) -> str | None:
    """What one exception in a failed model call's chain says it is, from its status, its provider body and its
    type; ``None`` when it says nothing."""
    from harness_core.models.transports.failures import NETWORK_ERRORS

    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        if status == HTTPStatus.REQUEST_TIMEOUT:
            return "timeout"
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            return "rate_limit"
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return "server_error"
    fields = _body_fields(exc)
    if any(hint in field for field in fields for hint in _CONTENT_FILTER_HINTS):
        return "content_filter"
    # OpenAI reports an exhausted account inside an accepted Responses stream as an error event whose message
    # ("You have no credits remaining") names no code; read as the stream's server error below, it was retried
    # and answered "the provider is not responding" instead of the quota.
    if any(field in _QUOTA_HINTS for field in fields):
        return "quota_exceeded"
    if any(field in _PROVIDER_SERVER_ERROR_TYPES for field in fields):
        return "server_error"
    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return "timeout"
    if isinstance(exc, NETWORK_ERRORS) or any(hint in name for hint in _NETWORK_ERROR_NAME_HINTS):
        return "server_error"
    if name == "apierror" and status is None and hasattr(exc, "body"):
        # The SDK's base error with no HTTP status is the provider's error event inside a stream it had already
        # accepted (openai's `Stream`): a refused request is answered with a status before any stream starts.
        return "server_error"
    return None


def classify_model_exception(exc: BaseException) -> str:
    """The error code for a failed model call, as the durable model port acts on it (FIX-R2 B4).

    ``classify_error`` reads the text, which says nothing for a failure whose class is in its type, status or
    body: a server error reported inside an accepted stream, a dropped connection, a read with no message, a
    529. Those read as ``unknown`` and failed the run with no retry; here the exception chain decides them.
    A failure nothing in the chain explains stays ``unknown``.
    """
    code, _ = classify_error(str(exc))
    if code != "unknown":
        return code
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        found = _class_of(current)
        if found is not None:
            return found
        current = current.__cause__ or current.__context__
    return "unknown"
def run_error_text(result: object) -> str | None:
    """What a run's user-facing answer is classified from: the raw text behind `AgentResult.error` (`error_detail`),
    else `error`. Duck-typed: a result that keeps no raw text (a stub, an older result type) gives its `error`."""
    return getattr(result, "error_detail", None) or getattr(result, "error", None)


__all__ = [
    "MCP_UNAVAILABLE",
    "classify_error",
    "classify_exception",
    "classify_model_exception",
    "model_call_failure_message",
    "register_error_classifier",
    "run_error_text",
]
