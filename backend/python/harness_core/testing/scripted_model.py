"""Deterministic model double driven by a :class:`ModelScript`.

Promoted from ``tests/unit/agents/adapter/support/scripted_transport.py``
without its ``agent_loop_lib`` coupling; adapters live in the product package.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from harness_core.testing.model_script import load_script
from harness_core.testing.model_types import (
    Completed,
    ModelCallError,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    ScriptExhausted,
    ScriptMismatch,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from harness_core.testing.model_script import Expect, ModelScript, Reply, Turn


def _check_expect(expect: Expect, request: ModelRequest, turn_index: int) -> None:
    def fail(reason: str) -> ScriptMismatch:
        return ScriptMismatch(f"turn {turn_index}: {reason}")

    if expect.last_user_contains is not None:
        user = request.last_said_by_user()
        if user is None or expect.last_user_contains not in user.text:
            raise fail(f"last user message does not contain {expect.last_user_contains!r}")
    if expect.tool_result_for is not None:
        result = request.last("tool")
        if result is None or result.tool_name != expect.tool_result_for:
            raise fail(f"last tool result is not for {expect.tool_result_for!r}")
        if expect.status is not None and result.tool_status != expect.status:
            raise fail(f"tool result status {result.tool_status!r} != {expect.status!r}")


def _tool_calls(reply: Reply, turn_index: int) -> tuple[ToolCall, ...]:
    return tuple(
        ToolCall(id=spec.id or f"call_{turn_index}_{i}", name=spec.name, args=dict(spec.args))
        for i, spec in enumerate(reply.tool_calls)
    )


class ScriptedModel:
    """Replays a script turn by turn; every call consumes exactly one turn.

    An ``error`` on a reply is raised after that reply's deltas, so a reply
    with both text and an error models a stream cut mid-response.
    """

    def __init__(
        self,
        script: ModelScript,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._script = script
        self._sleep = sleep
        self._next = 0
        self._lock = threading.Lock()
        self.requests: list[ModelRequest] = []
        self.usage_total = Usage()

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> ScriptedModel:
        return cls(load_script(path), sleep=sleep)

    @property
    def script_id(self) -> str:
        return self._script.id

    @property
    def remaining(self) -> int:
        return len(self._script.turns) - self._next

    def assert_exhausted(self) -> None:
        if self.remaining:
            raise AssertionError(f"{self.remaining} scripted turn(s) never requested")

    def _take_turn(self, request: ModelRequest) -> tuple[int, Turn]:
        with self._lock:
            index = self._next
            if index >= len(self._script.turns):
                raise ScriptExhausted(
                    f"script {self._script.id!r} has {index} turn(s); call {index + 1} is unscripted"
                )
            self._next += 1
            self.requests.append(request)
        return index, self._script.turns[index]

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        index, turn = self._take_turn(request)
        if turn.expect is not None:
            _check_expect(turn.expect, request, index)
        reply = turn.from_request(request) if turn.from_request else turn.reply
        assert reply is not None  # guaranteed by Turn validation
        if reply.delay_ms:
            await self._sleep(reply.delay_ms / 1000)
        if reply.reasoning:
            yield ReasoningDelta(reply.reasoning)
        for chunk in reply.text_chunks():
            yield TextDelta(chunk)
        self.usage_total = self.usage_total + reply.usage
        if reply.error is not None:
            err = reply.error
            raise ModelCallError(
                err.kind,
                err.message or err.kind,
                status=err.effective_status,
                retry_after_s=err.retry_after_s,
                retryable=err.retryable,
            )
        calls = _tool_calls(reply, index)
        for call in calls:
            yield ToolCallEvent(call)
        yield Completed(
            ModelResponse(
                turn_index=index,
                text=reply.content,
                reasoning=reply.reasoning,
                tool_calls=calls,
                usage=reply.usage,
                stop_reason=reply.resolved_stop_reason(),
            )
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if isinstance(event, Completed):
                return event.response
        raise AssertionError("stream ended without Completed")


__all__ = ["ScriptedModel"]
