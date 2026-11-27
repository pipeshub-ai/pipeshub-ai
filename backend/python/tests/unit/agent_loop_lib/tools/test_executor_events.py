"""``tool.call.completed``: one platform event per tool call from ``ToolExecutor.call_tool``, with the registered
tool's name, how the call ended and how long it took, and never an argument or an output."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.tools.base import ToolOutput
from app.core import org_context
from app.platform_events import catalog
from tests.support.platform_event_capture import captured_events, facts
from tests.unit.agent_loop_lib.tools.test_executor_logging import (
    _call,
    _clock,
    _Decide,
    _executor,
    _raises,
    _returns,
    _Tool,
)

pytestmark = pytest.mark.asyncio

SECRET = "SEEDED-argument-text"
OUTPUT = "SEEDED-output-text"
FIELDS = set(catalog.EVENTS["tool.call.completed"].fields)


async def test_a_call_is_announced_with_its_tool_and_outcome_and_no_argument_or_output() -> None:
    executor = _executor(_Tool(_returns(ToolOutput(success=True, data=OUTPUT))))
    with captured_events("tool.call.completed") as events, org_context.for_org("org_a", job="test"):
        await executor.call_tool(_call(q=SECRET))

    (call,) = facts(events, "tool.call.completed")
    assert set(call) == FIELDS
    assert (call["tool"], call["kind"], call["outcome"], call["org_id"]) == ("probe__run", "builtin", "success", "org_a")
    assert 0 <= call["duration_seconds"] < 5
    assert SECRET not in repr(call) and OUTPUT not in repr(call) and "call-1" not in repr(call)


async def test_a_failed_tool_and_one_that_raised_are_failures() -> None:
    with captured_events("tool.call.completed") as events:
        await _executor(_Tool(_returns(ToolOutput(success=False, error="no")))).call_tool(_call())
        await _executor(_Tool(_raises(KeyError(SECRET)))).call_tool(_call())

    assert [call["outcome"] for call in facts(events, "tool.call.completed")] == ["failure", "failure"]
    assert SECRET not in repr(events)


async def test_a_name_the_model_made_up_is_not_kept() -> None:
    with captured_events("tool.call.completed") as events:
        await _executor().call_tool(_call(name="made_up_" + SECRET))

    (call,) = facts(events, "tool.call.completed")
    assert (call["tool"], call["kind"], call["outcome"]) == ("unregistered", None, "failure")
    assert SECRET not in repr(call)


async def test_a_denied_call_is_skipped() -> None:
    kernel = _Decide.kernel(HookEvent.PRE_TOOL_USE, lambda ctx: ctx.deny("no"))
    with captured_events("tool.call.completed") as events:
        await _executor(_Tool(_returns(ToolOutput(success=True, data="ok"))), kernel=kernel).call_tool(_call())

    assert [call["outcome"] for call in facts(events, "tool.call.completed")] == ["skipped"]


async def test_the_clock_is_not_read_for_it_when_nobody_listens() -> None:
    with _clock(10.0, 11.5):  # the two readings of the call's own duration, and no third
        await _executor(_Tool(_returns(ToolOutput(success=True, data="ok")))).call_tool(_call())
