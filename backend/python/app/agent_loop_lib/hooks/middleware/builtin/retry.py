from __future__ import annotations

import asyncio
import random

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.transport.base import RetryConfig

"""Retries the LLM call with exponential backoff + jitter on retryable errors.

Registered on `HookRegistry.wrapper(HookEvent.PRE_MODEL_CALL)` rather than a
`Pipeline` — see `hooks/events.py`'s module docstring for why: a retry policy
needs to call "the rest of the chain" an arbitrary number of times, which a
Pipeline's single-pass `next()` can't express, but `Wrapper`'s nested-closure
composition can. Direct replacement for `RetryHook`.

Only retries `TransportError` instances with `.retryable is True` (set by the
transport based on the underlying SDK exception — see
`AnthropicTransport._wrap_error`). Non-`TransportError` exceptions and
explicitly non-retryable errors (auth, bad request, ...) propagate
immediately with no retry.
"""


def _is_retryable(exc: TransportError, config: RetryConfig) -> bool:
    if not exc.retryable:
        return False
    if exc.status_code is None:
        # Network/timeout errors have no HTTP status but were already
        # flagged retryable by the transport — trust that classification.
        return True
    return exc.status_code in config.retryable_status_codes


def retry_model_call(config: RetryConfig | None = None):
    cfg = config or RetryConfig()

    async def _wrapper(next_fn):
        delay = cfg.initial_delay
        last_exc: TransportError | None = None

        for attempt in range(cfg.max_retries + 1):
            try:
                return await next_fn()
            except TransportError as exc:
                if not _is_retryable(exc, cfg) or attempt >= cfg.max_retries:
                    raise
                last_exc = exc
                jitter = random.uniform(0, delay * 0.1)
                await asyncio.sleep(delay + jitter)
                delay = min(delay * cfg.backoff_factor, cfg.max_delay)

        # Unreachable: loop either returns or raises on the last attempt.
        assert last_exc is not None
        raise last_exc

    return _wrapper
