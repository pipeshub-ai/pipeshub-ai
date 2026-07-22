"""Regression tests for `agent/streaming.py`'s teardown-on-early-exit bug:
`stream()`'s generator used to `await task` unconditionally in its
`finally`, which — when the consumer stops iterating early (a client
disconnect cancelling the task driving this generator, e.g.
`stream_bridge.py`'s `producer.cancel()`) — blocked until the underlying
`agent.run()` finished on its own instead of actually being cancelled. A
disconnected client kept paying for every remaining LLM/tool call.

Exercises `stream()` directly against a minimal fake `agent` (no real
`Agent`/transport needed — `stream()` only touches `agent.event_emitter`,
`agent._event_emitter_override`, `agent._streaming`, `agent.run()`, and
`agent.last_stream_result`), matching the "isolate the scheduler/streaming
logic from a real Agent" pattern `test_spawn_scheduler.py` already uses.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_loop_lib.agent import streaming
from app.agent_loop_lib.core.types import Goal


class _FakeAgent:
    def __init__(self, *, run_seconds: float = 10.0) -> None:
        self.event_emitter = None
        self._event_emitter_override: Any = None
        self._streaming = False
        self.last_stream_result: Any = None
        self._run_seconds = run_seconds
        self.run_started = asyncio.Event()
        self.cancelled = False
        self.ran_to_completion = False

    async def run(self, goal: Goal, **_kwargs: Any) -> Any:
        self.run_started.set()
        try:
            await asyncio.sleep(self._run_seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.ran_to_completion = True
        return SimpleNamespace(goal=goal, success=True, output="done", error=None)


class TestAgentStream:
    async def test_normal_completion_sets_last_stream_result(self) -> None:
        agent = _FakeAgent(run_seconds=0.0)
        events = [event async for event in streaming.stream(agent, Goal(description="test"))]

        assert events == []
        assert agent.ran_to_completion is True
        assert agent.last_stream_result.output == "done"
        assert agent._streaming is False
        assert agent._event_emitter_override is None

    async def test_cancelling_the_consumer_cancels_the_underlying_run(self) -> None:
        """The fix: an early-cancelled consumer must cancel `agent.run()`
        itself, not merely stop reading events while `run()` keeps going
        to completion in the background."""
        agent = _FakeAgent(run_seconds=10.0)
        gen = streaming.stream(agent, Goal(description="test"))

        async def _consume() -> None:
            async for _ in gen:
                pass

        consumer = asyncio.create_task(_consume())
        await agent.run_started.wait()

        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        assert agent.cancelled is True
        assert agent.ran_to_completion is False
