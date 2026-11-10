"""A middleware that raises (`hooks/middleware/pipeline.py::_handle_middleware_error`): one classed
`middleware.failed` line, and a fail-closed pipeline still denies, with `code=middleware_error`."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import (
    AgentLifecycleContext,
    TurnContext,
)
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import Tool, ToolOutput
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.utils.logger import log_record_fields

pytestmark = pytest.mark.asyncio

_SENTINEL = "sentinel-mw-91"


class _Probe(Tool):
    name = property(lambda self: "probe__run")
    short_description = property(lambda self: "probe")
    description = property(lambda self: "probe")
    path = property(lambda self: "/toolsets/probe/probe__run")
    parameters = property(lambda self: [])

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return ToolOutput(success=True, data="ran")


async def _crashing_gate(ctx: Any, next_fn: Any) -> None:  # noqa: ANN401
    raise KeyError(_SENTINEL)


def _failed(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "middleware.failed"]


async def test_a_crashing_tool_gate_is_one_line_and_the_call_is_denied_with_a_code(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    kernel = HookRegistry()
    kernel.on(HookEvent.PRE_TOOL_USE).use(_crashing_gate)
    registry = ToolRegistry()
    registry.register_tool(_Probe())

    result = await ToolExecutor(registry, kernel).call_tool(ToolCall(id="c1", name="probe__run", arguments={}))

    assert result.is_error and result.content == f"middleware error: '{_SENTINEL}'"
    (failed,) = _failed(caplog)
    fields = log_record_fields(failed)
    assert (fields["context"], fields["middleware"], fields["error_class"]) == ("ToolCallContext", "_crashing_gate", "internal")
    assert "error_message" not in fields
    (end,) = [r for r in caplog.records if r.getMessage() == "tool_call.end"]
    assert log_record_fields(end)["deny_reason"] == "middleware_error"


@pytest.mark.parametrize("event,ctx", [
    (HookEvent.PRE_AGENT, AgentLifecycleContext()),
    (HookEvent.PRE_TURN, TurnContext(turn_index=0)),
])
async def test_a_crashing_run_or_turn_middleware_is_still_denied(event: HookEvent, ctx: Any, caplog: pytest.LogCaptureFixture) -> None:  # noqa: ANN401
    caplog.set_level(logging.DEBUG)
    kernel = HookRegistry()
    kernel.on(event).use(_crashing_gate)

    await kernel.on(event).dispatch(ctx)

    assert (ctx.decision, ctx.decision_code) == (PreDecision.DENY, "middleware_error")
    (failed,) = _failed(caplog)
    assert log_record_fields(failed)["context"] == type(ctx).__name__
