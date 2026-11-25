"""Capture a run's model and tool I/O into a :class:`Recording`.

The model side wraps anything that streams :mod:`model_types` events
(:class:`ScriptedModel`, or a product transport adapted to the neutral
shapes). The tool side is fed by whatever sits at the harness's tool
boundary: the product adapter calls :meth:`RunRecorder.tool` once per
executed call.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Literal, cast

from harness_core.testing.model_types import (
    Completed,
    ModelCallError,
    ModelRequest,
    ModelResponse,
)
from harness_core.testing.replay.recording import (
    FinalOutcome,
    Manifest,
    ModelStep,
    RecordedEffect,
    RecordedError,
    RecordedResponse,
    RecordedToolCall,
    Recording,
    RequestSummary,
    RunInputs,
    ToolStep,
    digest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable

    from pydantic import JsonValue

    from harness_core.testing.model_types import ModelEvent, StreamingModel

_LAST_USER_CHARS = 400


def summarize_request(
    request: ModelRequest, scrub: Callable[[str], str] = lambda text: text
) -> RequestSummary:
    """``scrub`` is applied before digesting, so a recording scrubbed with the
    same scrubber replays to the same digests."""
    last_user = request.last("user")
    last_tool = request.last("tool")
    body: list[JsonValue] = [
        [m.role, scrub(m.text), m.tool_name, m.tool_status]
        for m in request.messages
        if m.role != "system"
    ]
    tools = cast("list[JsonValue]", sorted(request.tools))
    return RequestSummary(
        message_count=len(request.messages),
        last_user=(scrub(last_user.text)[:_LAST_USER_CHARS] if last_user else ""),
        last_tool=last_tool.tool_name if last_tool else None,
        last_tool_status=last_tool.tool_status if last_tool else None,
        tools=sorted(request.tools),
        digest=digest({"messages": body, "tools": tools}),
    )


def record_response(response: ModelResponse) -> RecordedResponse:
    return RecordedResponse(
        text=response.text,
        reasoning=response.reasoning,
        tool_calls=[
            RecordedToolCall(id=c.id, name=c.name, args=dict(c.args))
            for c in response.tool_calls
        ],
        usage=response.usage,
        stop_reason=response.stop_reason,
    )


class RunRecorder:
    """Collects steps as they happen and orders them when the run ends.

    Parallel tool calls finish in any order, so a tool step is placed right
    after the model response that issued it, in that response's call order.
    That keeps two recordings of the same behaviour byte-identical.
    """

    def __init__(self, scrub: Callable[[str], str] | None = None) -> None:
        self._events: list[ModelStep | ToolStep] = []
        self._lock = threading.Lock()
        self._scrub = scrub

    def model(
        self,
        request: ModelRequest,
        response: ModelResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        recorded_error = None
        if error is not None:
            kind = (
                error.kind
                if isinstance(error, ModelCallError)
                else type(error).__name__
            )
            recorded_error = RecordedError(kind=kind, message=str(error)[:500])
        step = ModelStep(
            request=summarize_request(request, self._scrub)
            if self._scrub
            else summarize_request(request),
            response=record_response(response) if response is not None else None,
            error=recorded_error,
        )
        with self._lock:
            self._events.append(step)

    def tool(
        self,
        *,
        call_id: str,
        name: str,
        args: dict[str, JsonValue],
        status: Literal["succeeded", "failed", "denied"],
        result: JsonValue = None,
        error: str | None = None,
        sources: list[JsonValue] | None = None,
    ) -> None:
        step = ToolStep(
            call_id=call_id,
            name=name,
            args=args,
            status=status,
            result=result,
            error=error,
            sources=sources or [],
        )
        with self._lock:
            self._events.append(step)

    def ordered_steps(self) -> list[ModelStep | ToolStep]:
        with self._lock:
            events = list(self._events)
        tools = [e for e in events if isinstance(e, ToolStep)]
        placed: set[int] = set()
        out: list[ModelStep | ToolStep] = []
        for event in events:
            if isinstance(event, ToolStep):
                if id(event) not in placed:
                    placed.add(id(event))
                    out.append(event)
                continue
            out.append(event)
            for call in event.response.tool_calls if event.response else []:
                match = next(
                    (t for t in tools if t.call_id == call.id and id(t) not in placed),
                    None,
                )
                if match is not None:
                    placed.add(id(match))
                    out.append(match)
        return out

    def finish(
        self,
        manifest: Manifest,
        *,
        inputs: RunInputs | None = None,
        final: FinalOutcome | None = None,
        effects: Iterable[RecordedEffect] = (),
    ) -> Recording:
        return Recording(
            manifest=manifest,
            inputs=inputs or RunInputs(),
            steps=self.ordered_steps(),
            effects=list(effects),
            final=final or FinalOutcome(),
        )


class RecordingModel:
    """A model wrapper that records every call into a :class:`RunRecorder`.

    Satisfies the same ``stream``/``complete`` surface as :class:`ScriptedModel`
    so product adapters (``ScriptedModelTransport``) accept it unchanged.
    """

    def __init__(self, inner: StreamingModel, recorder: RunRecorder) -> None:
        self.inner = inner
        self.recorder = recorder

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        completed = False
        try:
            async for event in self.inner.stream(request):
                if isinstance(event, Completed):
                    completed = True
                    self.recorder.model(request, event.response)
                yield event
        except GeneratorExit:
            # The consumer stopped iterating (``complete`` returns on Completed).
            raise
        except BaseException as exc:
            if not completed:
                self.recorder.model(request, error=exc)
            raise

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if isinstance(event, Completed):
                return event.response
        raise AssertionError("stream ended without Completed")


__all__ = ["RecordingModel", "RunRecorder", "record_response", "summarize_request"]
