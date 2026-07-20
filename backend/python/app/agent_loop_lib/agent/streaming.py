from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, CompositeEmitter, EventEmitter

"""Streaming turn loop — `Agent.stream()` wraps the existing imperative
`run()` (entirely unchanged) rather than rewriting its control flow into a
native generator: a queue-backed `EventEmitter` captures every event
`run()` already emits — including the AG-UI-aliased ones and, when
`agent._streaming = True`, the real per-token TEXT_MESSAGE_* deltas — while
`run()` itself executes as a background asyncio Task. Streaming is a purely
additive observation layer bolted on from outside; it never touches the
shared `AgentRuntime.event_emitter` other agents/runs may be using
concurrently — only this one `Agent` instance's own emitter override.
"""

_SENTINEL = object()


class _QueueEmitter(EventEmitter):
    def __init__(self, queue: "asyncio.Queue[object]") -> None:
        self._queue = queue

    async def emit(self, event: AgentEvent) -> None:
        await self._queue.put(event)


async def stream(agent, goal: Goal, **run_kwargs) -> AsyncGenerator[AgentEvent, None]:
    queue: asyncio.Queue = asyncio.Queue()
    original_emitter = agent.event_emitter
    fanout = [original_emitter] if original_emitter is not None else []
    agent._event_emitter_override = CompositeEmitter([*fanout, _QueueEmitter(queue)])
    agent._streaming = True

    outcome: dict = {}

    async def _runner() -> None:
        try:
            outcome["result"] = await agent.run(goal, **run_kwargs)
        except BaseException as exc:
            outcome["exception"] = exc
        finally:
            agent._streaming = False
            # Restore the caller's original emitter (if any) so a second,
            # non-streaming run() on the same Agent instance afterwards
            # doesn't keep fanning events into this (by-then-abandoned) queue.
            agent._event_emitter_override = None
            await queue.put(_SENTINEL)

    task = asyncio.create_task(_runner())
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item
    finally:
        # If we're unwinding because the consumer stopped iterating early
        # (break / aclose(), e.g. a client disconnect cancelling the task
        # driving this generator — see `stream_bridge.py`), that same
        # cancellation must reach the underlying `agent.run()` task, not
        # just this generator's own `queue.get()` loop. Previously this was
        # an unconditional `await task`, which — on early exit — blocked
        # here until `run()` finished on its own, so a disconnected client
        # still paid for every remaining LLM/tool call. On normal
        # completion `task` is already done (the `_runner` finally already
        # put the sentinel), so `cancel()` is a no-op and this still just
        # awaits the (already finished) task.
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    agent.last_stream_result = outcome.get("result")
    if "exception" in outcome:
        raise outcome["exception"]
