"""What the gateway says when policy, not a fault, stops a model call.

A gateway that enforces per-user budgets and model access answers with the same
status codes a broken provider does (a budget refusal can arrive as 400, 401,
403 or 429 depending on the version), so status alone cannot tell "wait and
retry" from "this will not work for you". These helpers read the body and say
which it is, so callers can skip retries and fallbacks that cannot help and show
the user an accurate reason.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.services.model_identity import metrics

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

logger = logging.getLogger(__name__)

BUDGET_EXCEEDED_CODE = "budget_exceeded"
MODEL_ACCESS_DENIED_CODE = "model_access_denied"

_BUDGET_TYPES = frozenset({"budget_exceeded"})
_BUDGET_PHRASES = ("budget has been exceeded", "exceededbudget", "over budget", "max budget")
_MODEL_ACCESS_TYPE_SUFFIX = "model_access_denied"
_MODEL_ACCESS_PHRASES = (
    "not allowed to access model",
    "model access denied",
    "does not have access to model",
)
_ERROR_CODE_RE = re.compile(r"Error code:\s*(\d+)\s*-\s*", re.IGNORECASE)
_MAX_RETRY_AFTER_SECONDS = 3600.0


class GatewayPolicyKind(str, Enum):
    BUDGET = "budget"
    MODEL_ACCESS = "model_access"
    RATE_LIMIT = "rate_limit"


class GatewayPolicyError(Exception):
    """A refusal that retrying the same request will not change (or will change only later).

    Not a frozen dataclass: Python sets ``__traceback__`` on exceptions as they
    propagate, and a frozen ``__setattr__`` would turn that into an error.
    """

    def __init__(
        self,
        kind: GatewayPolicyKind,
        status: int | None = None,
        retry_after: float | None = None,
        reset_at: float | None = None,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(kind, status)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.reset_at = reset_at
        self.provider_code = provider_code

    @property
    def retryable(self) -> bool:
        """Only a rate limit comes right on its own, and only after the wait it names."""
        return self.kind is GatewayPolicyKind.RATE_LIMIT

    @property
    def error_code(self) -> str:
        return {
            GatewayPolicyKind.BUDGET: BUDGET_EXCEEDED_CODE,
            GatewayPolicyKind.MODEL_ACCESS: MODEL_ACCESS_DENIED_CODE,
            GatewayPolicyKind.RATE_LIMIT: "rate_limit",
        }[self.kind]

    def user_message(self, now: float | None = None) -> str:
        if self.kind is GatewayPolicyKind.BUDGET:
            wait = _wait_phrase(self.reset_at, now)
            tail = f" It resets {wait}." if wait else " It resets automatically."
            return (
                "Your usage limit for AI models has been reached."
                f"{tail} Ask a workspace admin if you need more."
            )
        if self.kind is GatewayPolicyKind.MODEL_ACCESS:
            return (
                "You don't have access to this AI model. Ask a workspace admin to "
                "give you access, or choose another model."
            )
        wait = _wait_phrase(self.reset_at, now)
        return f"The AI model is limiting requests right now. Try again {wait or 'in a minute'}."

    def __str__(self) -> str:
        return f"gateway refused the call ({self.kind.value}, status {self.status})"


def _wait_phrase(reset_at: float | None, now: float | None) -> str | None:
    if reset_at is None:
        return None
    remaining = reset_at - (time.time() if now is None else now)
    if remaining <= 0:
        return "shortly"
    if remaining < 90:
        return "in about a minute"
    if remaining < 5400:
        return f"in about {round(remaining / 60)} minutes"
    if remaining < 172800:
        return f"in about {round(remaining / 3600)} hours"
    return f"in about {round(remaining / 86400)} days"


def classify_gateway_error(
    status: int | None,
    body: Any,  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    headers: Mapping[str, str] | None = None,
    *,
    now: float | None = None,
) -> GatewayPolicyError | None:
    """The policy refusal this response is, or ``None`` if it is anything else."""
    error = _error_object(body)
    kind_hint = str(error.get("type") or "").lower()
    message = str(error.get("message") or "").lower()
    code = _clean_code(error.get("code") or error.get("type"))

    if kind_hint in _BUDGET_TYPES or any(p in message for p in _BUDGET_PHRASES):
        kind = GatewayPolicyKind.BUDGET
    elif kind_hint.endswith(_MODEL_ACCESS_TYPE_SUFFIX) or any(p in message for p in _MODEL_ACCESS_PHRASES):
        kind = GatewayPolicyKind.MODEL_ACCESS
    elif status == 429:
        kind = GatewayPolicyKind.RATE_LIMIT
    else:
        return None

    retry_after = _retry_after(headers, now)
    current = time.time() if now is None else now
    return GatewayPolicyError(
        kind=kind,
        status=status,
        retry_after=retry_after,
        reset_at=None if retry_after is None else current + retry_after,
        provider_code=code,
    )


def policy_error_from_exception(exc: BaseException) -> GatewayPolicyError | None:
    """``classify_gateway_error`` for an SDK exception, whatever shape it has.

    OpenAI-style exceptions carry ``status_code``, ``body`` and ``response``;
    LangChain and our own wrappers flatten them into a message such as
    ``Error code: 400 - {'error': {...}}``, which is parsed as a fallback.
    """
    for current in _chain(exc):
        if isinstance(current, GatewayPolicyError):
            return current
        status = getattr(current, "status_code", None)
        body = getattr(current, "body", None)
        response = getattr(current, "response", None)
        headers = getattr(response, "headers", None)
        if isinstance(status, int) and body is not None:
            hit = classify_gateway_error(status, body, headers)
            if hit is not None:
                return hit
        hit = classify_gateway_text(str(current))
        if hit is not None:
            return hit
    return None


def classify_gateway_text(message: str) -> GatewayPolicyError | None:
    """Classify an error already flattened to a string."""
    match = _ERROR_CODE_RE.search(message)
    if match is None:
        return None
    # SDKs append hints ("For troubleshooting, visit ...") after the body.
    start, end = message.find("{", match.end()), message.rfind("}")
    if start < 0 or end < start:
        return None
    return classify_gateway_error(int(match.group(1)), message[start : end + 1])


def classify_for_user(message: str) -> tuple[str, str] | None:
    """``(error_code, user_message)`` for a flattened budget or model-access refusal.

    A plain rate limit is left to the existing classifier, which already gives
    the right advice for it.
    """
    hit = classify_gateway_text(message)
    if hit is None or hit.kind is GatewayPolicyKind.RATE_LIMIT:
        return None
    return hit.error_code, hit.user_message()


def record_refusal(error: GatewayPolicyError, workload_class: str) -> None:
    metrics.inc(metrics.GATEWAY_POLICY_REFUSALS, error.kind.value, workload_class)


def _chain(exc: BaseException | None) -> Iterator[BaseException]:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _error_object(body: Any) -> dict[str, Any]:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    parsed = _parse_body(body)
    if not isinstance(parsed, dict):
        return {}
    inner = parsed.get("error")
    if isinstance(inner, dict):
        return inner
    if isinstance(inner, str):
        return {"message": inner, **{k: v for k, v in parsed.items() if k != "error"}}
    return parsed


def _parse_body(body: Any) -> Any:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", errors="replace")
    if not isinstance(body, str):
        return body
    text = body.strip()
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def _clean_code(value: Any) -> str | None:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    if value is None:
        return None
    text = str(value).strip()
    return text[:64] if text and text.lower() != "none" else None


def _retry_after(headers: Mapping[str, str] | None, now: float | None) -> float | None:
    if not headers:
        return None
    raw = None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(text).timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError):
            return None
    if seconds != seconds or seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


_ALERT_INTERVAL_SECONDS = 300.0
_last_alert: dict[str, float] = {}


def report_background_refusal(error: GatewayPolicyError, *, now: float | None = None) -> None:
    """Make a refusal of background work visible, without flooding the log.

    Indexing has no user to show a message to, and a service budget that is
    spent refuses every record until it resets. The throttle report lets the
    resource governor admit fewer records meanwhile; the error log, at most once
    per interval per kind, is what an operator alerts on.
    """
    from app.services.resource_governor.feedback import get_default_downstream_feedback

    get_default_downstream_feedback().report_throttle("llm_gateway")
    if error.kind is GatewayPolicyKind.RATE_LIMIT:
        return
    current = time.monotonic() if now is None else now
    last = _last_alert.get(error.kind.value)
    if last is not None and current - last < _ALERT_INTERVAL_SECONDS:
        return
    _last_alert[error.kind.value] = current
    logger.error(
        "The model gateway refused background work (%s). Indexing and extraction will keep "
        "failing until the service identity's %s is raised or reset.",
        error.kind.value,
        "budget" if error.kind is GatewayPolicyKind.BUDGET else "model access",
    )
