"""`retry_with_status` (`app/agents/agent_loop/hooks/retry_with_status.py`)
— the `PRE_MODEL_CALL` wrap-middleware that retries retryable
`TransportError`s (429/5xx/network) with backoff, emitting an SSE `status`
event through the request's `EventSink` before each retry sleep."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.transport.base import RetryConfig
from app.agents.agent_loop.hooks.retry_with_status import retry_with_status
from app.utils.logger import log_record_fields
from app.utils.logging_fields import current_log_fields


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


class _FailingSink:
    """Simulates a dead SSE connection — `write()` itself raises."""

    async def write(self, event: dict) -> bool:
        raise RuntimeError("connection closed")


def _fast_config(max_retries: int = 3) -> RetryConfig:
    # Zero-ish delays so these tests run instantly regardless of the
    # production defaults (1s initial delay, 2x backoff).
    return RetryConfig(max_retries=max_retries, initial_delay=0.001, backoff_factor=1.0, max_delay=0.001)


class TestRetryWithStatus:
    async def test_succeeds_without_retry_when_next_fn_succeeds(self) -> None:
        sink = _RecordingSink()
        middleware = retry_with_status(sink, _fast_config())

        async def _next() -> str:
            return "ok"

        result = await middleware(_next)

        assert result == "ok"
        assert sink.events == []

    async def test_retries_retryable_transport_error_then_succeeds(self) -> None:
        sink = _RecordingSink()
        middleware = retry_with_status(sink, _fast_config())
        calls = {"count": 0}

        async def _next() -> str:
            calls["count"] += 1
            if calls["count"] < 2:
                raise TransportError("rate limited", status_code=429, retryable=True)
            return "ok"

        result = await middleware(_next)

        assert result == "ok"
        assert calls["count"] == 2
        assert len(sink.events) == 1
        assert sink.events[0] == {
            "event": "status",
            "data": {
                "status": "retrying",
                "message": "The AI provider is rate limiting requests, retrying (2/4)...",
            },
        }

    async def test_raises_immediately_when_not_retryable(self) -> None:
        sink = _RecordingSink()
        middleware = retry_with_status(sink, _fast_config())

        async def _next() -> str:
            raise TransportError("bad request", status_code=400, retryable=False)

        with pytest.raises(TransportError):
            await middleware(_next)
        assert sink.events == []

    async def test_raises_after_exhausting_max_retries(self) -> None:
        sink = _RecordingSink()
        middleware = retry_with_status(sink, _fast_config(max_retries=2))
        calls = {"count": 0}

        async def _next() -> str:
            calls["count"] += 1
            raise TransportError("still limited", status_code=429, retryable=True)

        with pytest.raises(TransportError):
            await middleware(_next)

        assert calls["count"] == 3  # initial attempt + 2 retries
        assert len(sink.events) == 2  # a status event before each retry, none after the final raise

    async def test_non_transport_exceptions_propagate_untouched(self) -> None:
        sink = _RecordingSink()
        middleware = retry_with_status(sink, _fast_config())

        async def _next() -> str:
            raise ValueError("not a transport error")

        with pytest.raises(ValueError):
            await middleware(_next)
        assert sink.events == []

    async def test_none_event_sink_skips_sse_and_still_retries(self) -> None:
        middleware = retry_with_status(None, _fast_config())
        calls = {"count": 0}

        async def _next() -> str:
            calls["count"] += 1
            if calls["count"] < 2:
                raise TransportError("rate limited", status_code=429, retryable=True)
            return "ok"

        result = await middleware(_next)
        assert result == "ok"
        assert calls["count"] == 2

    async def test_event_sink_write_failure_does_not_break_retry(self) -> None:
        """A dead SSE connection must not prevent the retry itself from
        proceeding — only the (best-effort) status notification is lost."""
        middleware = retry_with_status(_FailingSink(), _fast_config())
        calls = {"count": 0}

        async def _next() -> str:
            calls["count"] += 1
            if calls["count"] < 2:
                raise TransportError("rate limited", status_code=429, retryable=True)
            return "ok"

        result = await middleware(_next)
        assert result == "ok"
        assert calls["count"] == 2

    async def test_529_overloaded_is_retried_under_default_config(self) -> None:
        """Regression: Anthropic's `overloaded_error` (HTTP 529) used to be
        absent from `RetryConfig.retryable_status_codes`' default, so a
        transport that correctly flagged it `retryable=True` still got
        re-raised immediately here because 529 failed the second check
        (`exc.status_code in config.retryable_status_codes`). Uses a real
        default `RetryConfig()` (only the delays shortened) rather than
        `_fast_config()`'s status-code list, since the bug was specifically
        in the default list."""
        default_cfg = RetryConfig(initial_delay=0.001, backoff_factor=1.0, max_delay=0.001)
        sink = _RecordingSink()
        middleware = retry_with_status(sink, default_cfg)
        calls = {"count": 0}

        async def _next() -> str:
            calls["count"] += 1
            if calls["count"] < 2:
                raise TransportError("Overloaded", status_code=529, retryable=True)
            return "ok"

        result = await middleware(_next)

        assert result == "ok"
        assert calls["count"] == 2
        assert "temporary error (529)" in sink.events[0]["data"]["message"]

    async def test_network_error_with_no_status_code_is_retried(self) -> None:
        sink = _RecordingSink()
        middleware = retry_with_status(sink, _fast_config())
        calls = {"count": 0}

        async def _next() -> str:
            calls["count"] += 1
            if calls["count"] < 2:
                raise TransportError("connection reset", status_code=None, retryable=True)
            return "ok"

        result = await middleware(_next)
        assert result == "ok"
        assert "temporary connection issue" in sink.events[0]["data"]["message"]


def _retry_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "llm_call.retry"]


def _failing(*errors: TransportError, seen: list[object] | None = None):  # noqa: ANN202
    remaining = list(errors)

    async def _next() -> str:
        if seen is not None:
            seen.append(current_log_fields().get("attempt"))
        if remaining:
            raise remaining.pop(0)
        return "ok"

    return _next


class TestRetriesAreLogged:
    async def test_a_429_then_success_logs_one_retry_with_what_was_slept(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        sink = _RecordingSink()
        config = RetryConfig(initial_delay=1.0)
        sleep = AsyncMock()
        with (
            patch("app.agents.agent_loop.hooks.retry_with_status.asyncio.sleep", new=sleep),
            patch("app.agents.agent_loop.hooks.retry_with_status.random.uniform", return_value=0.043),
            caplog.at_level(logging.DEBUG),
        ):
            result = await retry_with_status(sink, config)(
                _failing(TransportError("rate limited", status_code=429, retryable=True)),
            )

        assert result == "ok"
        assert len(sink.events) == 1  # the client is still told
        [record] = _retry_lines(caplog)
        assert record.levelno == logging.WARNING
        assert log_record_fields(record) == {
            "attempt": 1, "max_attempts": 4, "status": 429, "error_class": "rate_limited", "delay_ms": 1043,
        }
        sleep.assert_awaited_once_with(1.043)

    async def test_a_non_retryable_failure_logs_no_retry(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG), pytest.raises(TransportError):
            await retry_with_status(None, _fast_config())(
                _failing(TransportError("bad key", status_code=401)),
            )

        assert _retry_lines(caplog) == []

    async def test_exhausted_retries_log_one_line_per_retry_then_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        errors = [TransportError("down", status_code=503, retryable=True) for _ in range(3)]
        with caplog.at_level(logging.DEBUG), pytest.raises(TransportError):
            await retry_with_status(None, _fast_config(max_retries=2))(_failing(*errors))

        assert [log_record_fields(r)["attempt"] for r in _retry_lines(caplog)] == [1, 2]
        assert {log_record_fields(r)["error_class"] for r in _retry_lines(caplog)} == {"unavailable"}

    async def test_the_attempt_is_bound_during_each_call_and_not_after(self) -> None:
        seen: list[object] = []

        await retry_with_status(None, _fast_config())(
            _failing(TransportError("rate limited", status_code=429, retryable=True), seen=seen),
        )

        assert seen == [1, 2]
        assert "attempt" not in current_log_fields()

    async def test_concurrent_calls_keep_their_own_attempt(self) -> None:
        seen_a: list[object] = []
        seen_b: list[object] = []
        middleware = retry_with_status(None, _fast_config())

        await asyncio.gather(
            middleware(_failing(TransportError("rate limited", status_code=429, retryable=True), seen=seen_a)),
            middleware(_failing(seen=seen_b)),
        )

        assert (seen_a, seen_b) == ([1, 2], [1])

    async def test_work_started_after_a_retried_call_carries_no_attempt(self) -> None:
        await retry_with_status(None, _fast_config())(
            _failing(TransportError("rate limited", status_code=429, retryable=True)),
        )

        later = asyncio.create_task(asyncio.sleep(0, result=current_log_fields().get("attempt")))
        assert await later is None
