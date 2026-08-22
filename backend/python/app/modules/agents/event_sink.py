"""`EventSink` protocol: "write one SSE-shaped event dict" behind a single
`write()` coroutine, so downstream code (status/keepalive/error helpers,
`RespondPipeline`, etc.) can run unmodified regardless of which concrete
sink is driving the stream (`QueueEventSink` for the agent loop, see
`app/agents/agent_loop/stream_bridge.py`; `SSEEventEmitter`, see
`app/agents/agent_loop/sse_emitter.py`).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventSink(Protocol):
    """Anything that can accept one SSE-shaped `{"event": ..., "data": ...}` dict."""

    async def write(self, event: dict[str, Any]) -> bool:
        """Emit `event`. Returns True if the write succeeded, False otherwise."""
        ...
