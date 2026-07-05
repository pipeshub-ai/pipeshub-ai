"""`retry_with_status` (`app/agents/agent_loop/hooks/retry_with_status.py`)
— the `PRE_MODEL_CALL` wrap-middleware that retries retryable
`TransportError`s (429/5xx/network) with backoff, emitting an SSE `status`
event through the request's `EventSink` before each retry sleep."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.transport.base import RetryConfig
from app.agents.agent_loop.hooks.retry_with_status import retry_with_status


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
