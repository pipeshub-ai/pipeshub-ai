"""`EventSink` protocol: the streaming-bridge abstraction introduced in
Phase 0 of the agent-loop migration.

`stream_utils.py`'s `safe_stream_write`/`stream_status`/`send_keepalive`/
`stream_error` all take a LangGraph `StreamWriter` + `RunnableConfig` pair.
`EventSink` abstracts "write one SSE-shaped event dict" behind a single
`write()` coroutine so the same downstream code (status/keepalive/error
helpers, and later `RespondPipeline`) can run unmodified whether the caller
is the legacy LangGraph graph (`LangGraphEventSink`, wrapping `StreamWriter`)
or the agent-loop adapter (`SSEEventEmitter`, see
`app/agents/agent_loop/sse_emitter.py`).
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter


@runtime_checkable
class EventSink(Protocol):
    """Anything that can accept one SSE-shaped `{"event": ..., "data": ...}` dict."""

    async def write(self, event: dict[str, Any]) -> bool:
        """Emit `event`. Returns True if the write succeeded, False otherwise."""
        ...


class LangGraphEventSink:
    """Adapts a LangGraph `StreamWriter` + `RunnableConfig` pair to `EventSink`.

    Thin wrapper around the existing `safe_stream_write` — no behavior
    change for the legacy graph path, just a uniform interface so
    `stream_status`/`send_keepalive`/`stream_error` (and, eventually,
    `RespondPipeline`) can be written once against `EventSink` instead of
    against `(writer, config)` pairs directly.
    """

    def __init__(self, writer: Optional[StreamWriter], config: Optional[RunnableConfig] = None) -> None:
        self._writer = writer
        self._config = config

    async def write(self, event: dict[str, Any]) -> bool:
        from app.modules.agents.qna.stream_utils import safe_stream_write

        return safe_stream_write(self._writer, event, self._config)
