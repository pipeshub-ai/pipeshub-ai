"""Adapts :mod:`harness_core.testing` doubles to ``agent_loop_lib``.

* :class:`ScriptedModelTransport` - a ``ScriptedModel`` (or any
  ``StreamingModel``, e.g. a ``replay.RecordingModel``) as an ``LLMTransport``,
  so legacy-loop tests run from the same YAML scripts.
* :class:`RecordingTransport` - records a real transport's calls into a
  ``replay.RunRecorder`` (recorded live model runs).
* :func:`tool_boundary` - records every tool execution at ``ToolExecutor`` and
  optionally serves frozen outcomes from a recording instead of running tools.

Test-only: nothing in the product imports this module.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast, override

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    Part,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.transport.base import LLMTransport
from harness_core.models.output_bound import output_limit
from harness_core.testing import (
    Completed,
    ModelCallError,
    ModelRequest,
    ReasoningDelta,
    RequestMessage,
    TextDelta,
    Usage,
)
from harness_core.testing import ModelResponse as NeutralResponse
from harness_core.testing import ToolCall as NeutralToolCall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    from pydantic import JsonValue

    from app.agent_loop_lib.core.tool_schema import ToolChoice, ToolSchema
    from app.agent_loop_lib.core.types import ToolCall as CoreToolCall
    from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
    from harness_core.testing import StreamingModel
    from harness_core.testing.replay import FrozenTools, RunRecorder

_STOP_REASONS = {reason.value: reason for reason in StopReason}


def _text(content: str | list[Part]) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.text for p in content if isinstance(p, TextPart))


def to_request(
    messages: list[Message], tools: list[ToolSchema] | None, system: str | None
) -> ModelRequest:
    call_names: dict[str, str] = {}
    out: list[RequestMessage] = [RequestMessage("system", system)] if system else []
    for message in messages:
        if isinstance(message, AssistantMessage):
            call_names.update({c.id: c.name for c in message.tool_calls or []})
            out.append(RequestMessage("assistant", message.text))
        elif isinstance(message, ToolMessage):
            out.append(
                RequestMessage(
                    "tool",
                    _text(message.content),
                    tool_name=call_names.get(message.tool_call_id or ""),
                    tool_status="failed" if message.is_error else "succeeded",
                )
            )
        elif isinstance(message, UserMessage):
            out.append(RequestMessage("user", _text(message.content), injected=message.injected))
        else:
            out.append(RequestMessage("system", message.content))
    return ModelRequest(tuple(out), tuple(t.name for t in tools or []))


def _usage(usage: Usage) -> TokenUsage:
    return TokenUsage(**usage.model_dump())


def to_model_response(response: NeutralResponse, model: str) -> ModelResponse:
    message = AssistantMessage(
        content=[TextPart(text=response.text)] if response.text else [],
        tool_calls=[
            ToolCall(id=c.id, name=c.name, arguments=dict(c.args)) for c in response.tool_calls
        ]
        or None,
        truncated=response.stop_reason == StopReason.MAX_TOKENS.value,
    )
    return ModelResponse(
        message=message,
        usage=_usage(response.usage),
        stop_reason=_STOP_REASONS.get(response.stop_reason, StopReason.END_TURN),
        model=model,
    )


def _transport_error(err: ModelCallError) -> TransportError:
    return TransportError(str(err), status_code=err.status, retryable=err.retryable)


def from_model_response(response: ModelResponse, turn_index: int = 0) -> NeutralResponse:
    message = response.message
    calls = tuple(
        NeutralToolCall(id=c.id, name=c.name, args=cast("dict[str, JsonValue]", jsonable(c.arguments)))
        for c in message.tool_calls or []
    )
    usage = response.usage
    return NeutralResponse(
        turn_index=turn_index,
        text=message.text or "",
        reasoning="",
        tool_calls=calls,
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        ),
        stop_reason=response.stop_reason.value,
    )


def jsonable(value: object) -> JsonValue:
    """A JSON-safe copy; anything JSON cannot hold becomes its ``str``."""
    return cast("JsonValue", json.loads(json.dumps(value, default=str)))


class ScriptedModelTransport(LLMTransport):
    def __init__(self, model: StreamingModel, model_name: str = "scripted-model") -> None:
        super().__init__()
        self.scripted = model
        self._model = model_name

    @property
    @override
    def provider(self) -> str:
        return "scripted"

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        try:
            response = await self.scripted.complete(to_request(messages, tools, system))
        except ModelCallError as err:
            raise _transport_error(err) from err
        return to_model_response(response, self._model)

    @override
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        try:
            response = await self.scripted.complete(to_request(messages, None, system))
        except ModelCallError as err:
            raise _transport_error(err) from err
        data: dict[str, Any]
        if response.tool_calls:
            data = dict(response.tool_calls[0].args)
        else:
            data = cast("dict[str, Any]", json.loads(response.text)) if response.text else {}
        return StructuredResponse(data=data, usage=_usage(response.usage), model=self._model)

    @override
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            async for event in self.scripted.stream(to_request(messages, tools, system)):
                if isinstance(event, TextDelta):
                    yield TextDeltaEvent(delta=event.text)
                elif isinstance(event, ReasoningDelta):
                    yield ThinkingDeltaEvent(delta=event.text)
                elif isinstance(event, Completed):
                    yield StreamCompleteEvent(
                        response=to_model_response(event.response, self._model)
                    )
        except ModelCallError as err:
            raise _transport_error(err) from err


class RecordingTransport(LLMTransport):
    """Passes every call to ``inner`` and records it into ``recorder``."""

    def __init__(self, inner: LLMTransport, recorder: RunRecorder) -> None:
        super().__init__()
        self.inner = inner
        self.recorder = recorder
        self._turn = 0

    @property
    @override
    def provider(self) -> str:
        return self.inner.provider

    @property
    @override
    def model_name(self) -> str:
        return self.inner.model_name

    @property
    @override
    def accepts_output_limit(self) -> bool:
        # The base's False made both loops' TransportModel send every live model call unbounded (P4.17L LV-3).
        return self.inner.accepts_output_limit

    def _record(self, request: ModelRequest, response: ModelResponse) -> None:
        self.recorder.model(request, from_model_response(response, self._turn))
        self._turn += 1

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        request = to_request(messages, tools, system)
        try:
            response = await self.inner.complete(
                messages, tools=tools, system=system, model=model,
                thinking_budget=thinking_budget, effort=effort, system_blocks=system_blocks,
                **output_limit(max_output_tokens),
                **({"tool_choice": tool_choice} if tool_choice else {}),
            )
        except Exception as err:
            self.recorder.model(request, error=err)
            raise
        self._record(request, response)
        return response

    @override
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> StructuredResponse:
        request = to_request(messages, None, system)
        try:
            response = await self.inner.complete_structured(
                messages, output_schema, system=system, model=model, **output_limit(max_output_tokens)
            )
        except Exception as err:
            self.recorder.model(request, error=err)
            raise
        self.recorder.model(
            request,
            NeutralResponse(
                turn_index=self._turn,
                text=json.dumps(jsonable(response.data), sort_keys=True),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                reasoning="",
                tool_calls=(),
                usage=Usage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cache_read_tokens=response.usage.cache_read_tokens,
                    cache_write_tokens=response.usage.cache_write_tokens,
                ),
                stop_reason="end_turn",
            ),
        )
        self._turn += 1
        return response

    @override
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        request = to_request(messages, tools, system)
        try:
            async for event in self.inner.stream(
                messages, tools=tools, system=system, model=model,
                thinking_budget=thinking_budget, effort=effort, system_blocks=system_blocks,
                **output_limit(max_output_tokens),
                **({"tool_choice": tool_choice} if tool_choice else {}),
            ):
                if isinstance(event, StreamCompleteEvent):
                    self._record(request, event.response)
                yield event
        except GeneratorExit:
            raise
        except Exception as err:
            self.recorder.model(request, error=err)
            raise


@dataclass
class ToolBoundaryStats:
    """What the harness did at the tool boundary, beyond the recorded steps."""

    executed: int = 0
    denied: int = 0
    approval_prompts: int = 0
    approvals_granted: int = 0


def _tool_output(status: str, result: JsonValue, error: str | None, sources: tuple[JsonValue, ...]) -> ToolOutput:
    return ToolOutput(
        success=status == "succeeded",
        data=result,
        error=error,
        sources=[Source.model_validate(s) for s in sources],
    )


@contextmanager
def tool_boundary(
    recorder: RunRecorder | None = None,
    frozen: FrozenTools | None = None,
    approve: Callable[[CoreToolCall, str], Awaitable[bool]] | None = None,
    freeze: Callable[[str], bool] = lambda _name: True,
) -> Iterator[ToolBoundaryStats]:
    """Record (and with ``frozen``, replay) every tool execution.

    Only tools ``freeze`` selects are served from the recording: the outside
    world (connectors, sandbox). Harness-internal tools (planning, critique,
    verification) run for real because they call the model themselves and
    those calls are part of the recording. Tool-loop special routes
    (``spawn_agent``, ``clarify`` ...) always run for real for the same reason.
    Every tool is recorded either way. A call that
    PRE_TOOL_USE refuses never reaches ``_run`` and is recorded as ``denied``.
    ``approve`` answers PRE_TOOL_USE ``ask`` decisions when the harness wired no
    one to ask; every ask is counted either way.
    """
    stats = ToolBoundaryStats()
    reached_run: set[str] = set()
    original_run = ToolExecutor._run  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportUnknownVariableType]
    original_call = ToolExecutor.call_tool

    async def _run(
        self: ToolExecutor,
        call: CoreToolCall,
        resolved_name: str,
        tool_input: dict[str, Any],
        override_execute: Any,  # noqa: ANN401 - mirrors ToolExecutor._run
    ) -> ToolOutput:
        reached_run.add(call.id)
        stats.executed += 1
        args = cast("dict[str, JsonValue]", jsonable(tool_input))
        if frozen is not None and override_execute is None and freeze(call.name):
            outcome = frozen.lookup(call.id, call.name, args)
            output = _tool_output(outcome.status, outcome.result, outcome.error, outcome.sources)
        else:
            if frozen is not None:
                frozen.lookup(call.id, call.name, args)
            output = await original_run(self, call, resolved_name, tool_input, override_execute)
        if recorder is not None:
            recorder.tool(
                call_id=call.id,
                name=call.name,
                args=args,
                status="succeeded" if output.success else "failed",
                result=jsonable(output.data),
                error=output.error,
                sources=[jsonable(s.model_dump(mode="json")) for s in output.sources],
            )
        return output

    async def call_tool(self: ToolExecutor, call: CoreToolCall, **kwargs: Any) -> CoreToolResult:  # noqa: ANN401
        on_ask = kwargs.get("on_ask")

        async def counted_ask(ask_call: CoreToolCall, reason: str) -> bool:
            stats.approval_prompts += 1
            if on_ask is not None:
                granted = await on_ask(ask_call, reason)
            elif approve is not None:
                granted = await approve(ask_call, reason)
            else:
                granted = False
            stats.approvals_granted += int(granted)
            return granted

        kwargs["on_ask"] = counted_ask
        result = await original_call(self, call, **kwargs)
        if call.id not in reached_run and result.is_error:
            stats.denied += 1
            if recorder is not None:
                recorder.tool(
                    call_id=call.id,
                    name=call.name,
                    args=cast("dict[str, JsonValue]", jsonable(call.arguments)),
                    status="denied",
                    error=str(result.content),
                )
        return result

    ToolExecutor._run = _run  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    ToolExecutor.call_tool = call_tool  # type: ignore[method-assign]
    try:
        yield stats
    finally:
        ToolExecutor._run = original_run  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
        ToolExecutor.call_tool = original_call  # type: ignore[method-assign]


__all__ = [
    "RecordingTransport",
    "ScriptedModelTransport",
    "ToolBoundaryStats",
    "from_model_response",
    "jsonable",
    "to_model_response",
    "to_request",
    "tool_boundary",
]
