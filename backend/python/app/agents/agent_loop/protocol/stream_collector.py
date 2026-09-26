"""Drains a chat SSE stream into one terminal outcome for the non-streaming
routes (`chatbot.py::askAI`, `agent.py::chat`).

Both routes run the exact pipeline their `/stream` sibling runs and only
differ in how the result leaves the process, so the pipeline is never
duplicated -- only the framing is undone here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from app.agents.agent_loop.protocol.agui import AGUIEventType

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Awaitable, Callable, Iterator

_COMPLETION_EVENTS = frozenset({"complete", AGUIEventType.RUN_FINISHED.value})
_ERROR_EVENTS = frozenset({"error", AGUIEventType.RUN_ERROR.value})

NO_RESPONSE_MESSAGE = "The assistant did not produce a response. Please try again."
CLIENT_DISCONNECTED_MESSAGE = "The request was cancelled before the answer finished."

# User-facing codes from `error_classification.classify_exception` and the
# chat routes. Node only shows the message of a sub-500 reply, so codes whose
# message tells the user what to do map below 500; the rest stay generic.
_ERROR_CODE_STATUS: dict[str, int] = {
    "invalid_request": 400,
    "request_failed": 400,
    "request_too_large": 413,
    "content_filter": 422,
    "llm_not_configured": 424,
    "llm_initialization_failed": 424,
    "toolset_config_missing": 424,
    "mcp_server_config_missing": 424,
    "rate_limit": 429,
    "quota_exceeded": 429,
    "auth_error": 502,
    "server_error": 502,
    "timeout": 504,
    "client_disconnected": 499,
}
_DEFAULT_ERROR_STATUS = 500


@dataclass(frozen=True)
class SSEFrame:
    event: str
    data: Any


class SSEFrameParser:
    """Incremental `event:`/`data:` parser. Buffers across chunks, so a frame
    split over two `body_iterator` chunks is parsed once it is whole."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str | bytes) -> Iterator[SSEFrame]:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        self._buffer += text.replace("\r\n", "\n")
        *blocks, self._buffer = self._buffer.split("\n\n")
        for block in blocks:
            parsed = _parse_block(block)
            if parsed is not None:
                yield parsed

    def flush(self) -> Iterator[SSEFrame]:
        """A stream may end without the final blank line."""
        remainder, self._buffer = self._buffer, ""
        parsed = _parse_block(remainder)
        if parsed is not None:
            yield parsed


def _parse_block(block: str) -> SSEFrame | None:
    event_name: str | None = None
    data_lines: list[str] = []
    for line in block.split("\n"):
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].removeprefix(" "))
    if event_name is None or not data_lines:
        return None
    try:
        return SSEFrame(event_name, json.loads("\n".join(data_lines)))
    except json.JSONDecodeError:
        return None


@dataclass(frozen=True)
class StreamOutcome:
    completion: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_response(self) -> JSONResponse:
        if self.completion is not None:
            return JSONResponse(content=self.completion)
        if self.error is not None:
            return _error_response(self.error)
        return _error_response({"message": NO_RESPONSE_MESSAGE, "code": "no_response"})


def _error_response(error: dict[str, Any]) -> JSONResponse:
    code = error.get("code") if isinstance(error.get("code"), str) else None
    return JSONResponse(
        status_code=_status_for_error(error, code),
        content={
            "status": "error",
            "code": code or "unknown",
            "message": error.get("message") or error.get("error") or NO_RESPONSE_MESSAGE,
            "searchResults": [],
            "records": [],
        },
    )


def _status_for_error(error: dict[str, Any], code: str | None) -> int:
    explicit = error.get("status_code")
    if isinstance(explicit, int) and 400 <= explicit <= 599:
        return explicit
    return _ERROR_CODE_STATUS.get(code or "", _DEFAULT_ERROR_STATUS)


def _completion_from(frame: SSEFrame) -> dict[str, Any] | None:
    if frame.event == AGUIEventType.RUN_FINISHED.value:
        result = frame.data.get("result")
        return result if isinstance(result, dict) else None
    return frame.data


async def collect_stream_outcome(
    body_iterator: AsyncIterable[str | bytes],
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> StreamOutcome:
    """Consumes the whole stream and returns its root run's LAST terminal frame.

    Frames carrying `parentRunId` belong to sub-agents: their RUN_ERROR is
    handed back to the parent as a tool result and the parent still answers,
    so only root frames decide the outcome. The last root frame wins both
    ways: a RUN_ERROR after RUN_FINISHED means the answer was not saved, and
    a graceful RUN_FINISHED after a RUN_ERROR (see `agui_emitter.py`) is the
    run's real result.

    `is_disconnected` is checked between chunks so a caller that gave up
    stops the agent loop instead of leaving it to run to completion unseen.
    The iterator is always closed, which runs the generator's own cleanup.
    """
    parser = SSEFrameParser()
    completion: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def absorb(frames: Iterator[SSEFrame]) -> None:
        nonlocal completion, error
        for frame in frames:
            if not isinstance(frame.data, dict) or frame.data.get("parentRunId") is not None:
                continue
            if frame.event in _COMPLETION_EVENTS:
                result = _completion_from(frame)
                if result is not None:
                    completion, error = result, None
            elif frame.event in _ERROR_EVENTS:
                completion, error = None, frame.data

    iterator = body_iterator.__aiter__()
    try:
        async for chunk in iterator:
            absorb(parser.feed(chunk))
            if completion is None and is_disconnected is not None and await is_disconnected():
                return StreamOutcome(
                    error={"message": CLIENT_DISCONNECTED_MESSAGE, "code": "client_disconnected"},
                )
        absorb(parser.flush())
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()
    return StreamOutcome(completion=completion, error=error)


__all__ = [
    "SSEFrame",
    "SSEFrameParser",
    "StreamOutcome",
    "collect_stream_outcome",
    "NO_RESPONSE_MESSAGE",
]
