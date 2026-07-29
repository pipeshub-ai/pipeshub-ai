"""`retry_with_status`: PipesHub-specific `PRE_MODEL_CALL` wrap middleware.

Adapts `agent_loop_lib`'s generic `retry_model_call` (see
`app.agent_loop_lib.hooks.middleware.builtin.retry`) to also push an SSE
`status` event through the request's `EventSink` before each backoff sleep,
so a rate-limited (429) or transiently-unavailable (5xx/network) LLM call
shows the user "retrying..." feedback instead of going silent for the whole
backoff window. Kept in the adapter layer (not agent_loop_lib) because
`EventSink`/the SSE payload shape are PipesHub-specific — agent_loop_lib's
own `RetryHook` has no concept of a user-facing stream.

Registered onto `HookRegistry.wrapper(HookEvent.PRE_MODEL_CALL)` by
`PipesHubAgentFactory._build_hooks()` — see that module for wiring.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.transport.base import RetryConfig

if TYPE_CHECKING:
    from app.agent_loop_lib.core.responses import ModelResponse
    from app.agent_loop_lib.hooks.middleware.wrapper import WrapMiddleware, WrapNext
    from app.modules.agents.event_sink import EventSink

logger = logging.getLogger(__name__)


def _is_retryable(exc: TransportError, config: RetryConfig) -> bool:
    """Mirrors `retry.py::_is_retryable` — duplicated rather than imported
    so this module depends only on the shared `TransportError`/`RetryConfig`
    types, not on agent_loop_lib's builtin retry hook internals."""
    if not exc.retryable:
        return False
    if exc.status_code is None:
        # Network/timeout errors have no HTTP status but were already
        # flagged retryable by the transport — trust that classification.
        return True
    return exc.status_code in config.retryable_status_codes


def _retry_status_message(exc: TransportError, attempt: int, max_attempts: int) -> str:
    if exc.status_code == 429:
        reason = "The AI provider is rate limiting requests"
    elif exc.status_code:
        reason = f"The AI provider returned a temporary error ({exc.status_code})"
    else:
        reason = "A temporary connection issue occurred"
    return f"{reason}, retrying ({attempt}/{max_attempts})..."


def retry_with_status(
    event_sink: "EventSink | None", config: RetryConfig | None = None
) -> "WrapMiddleware[ModelResponse]":
    """Returns a `PRE_MODEL_CALL` wrap-middleware: retries `TransportError`s
    flagged `retryable=True` with exponential backoff + jitter (same policy
    as `retry_model_call`), emitting a `status` SSE event via `event_sink`
    before each sleep so the frontend's existing status-spinner UI (see
    `SSEEventEmitter`) shows retry progress instead of appearing to hang.

    `event_sink=None` (e.g. non-streaming/background runs) silently skips
    the SSE emission and just retries.
    """
    cfg = config or RetryConfig()

    async def _wrapper(next_fn: "WrapNext[ModelResponse]") -> "ModelResponse":
        delay = cfg.initial_delay
        last_exc: TransportError | None = None

        for attempt in range(cfg.max_retries + 1):
            try:
                return await next_fn()
            except TransportError as exc:
                if not _is_retryable(exc, cfg) or attempt >= cfg.max_retries:
                    raise
                last_exc = exc
                if event_sink is not None:
                    try:
                        await event_sink.write({
                            "event": "status",
                            "data": {
                                "status": "retrying",
                                "message": _retry_status_message(
                                    exc, attempt + 2, cfg.max_retries + 1
                                ),
                            },
                        })
                    except Exception:
                        logger.debug("retry_with_status: event_sink.write failed", exc_info=True)
                jitter = random.uniform(0, delay * 0.1)
                await asyncio.sleep(delay + jitter)
                delay = min(delay * cfg.backoff_factor, cfg.max_delay)

        # Unreachable: loop either returns or raises on the last attempt.
        assert last_exc is not None
        raise last_exc

    return _wrapper


__all__ = ["retry_with_status"]
