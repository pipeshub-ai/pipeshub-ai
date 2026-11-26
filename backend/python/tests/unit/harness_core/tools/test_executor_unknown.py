"""AM-5..AM-7: the executor turns a failure after an ambiguous send into ``EffectUnknown`` when asked."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, override

import pytest

from harness_core.effects import EffectUnknown, note_ambiguous_send
from harness_core.kernel.core.types import ToolCall
from harness_core.tools.base import Tool, ToolOutput, ToolParameter
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ScriptedTool(Tool):
    """A tool whose ``execute`` is the given coroutine function, counted."""

    def __init__(self, name: str, body: Callable[[], Awaitable[ToolOutput]]) -> None:
        self._name = name
        self._body = body
        self.calls = 0

    @property
    @override
    def name(self) -> str:
        return self._name

    @property
    @override
    def short_description(self) -> str:
        return self._name

    @property
    @override
    def description(self) -> str:
        return self._name

    @property
    @override
    def path(self) -> str:
        return f"/tools/crm/{self._name}"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return []

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        self.calls += 1
        return await self._body()


async def swallowed_timeout() -> ToolOutput:
    """What connector tools do: the client marks the send, the tool returns an error."""
    note_ambiguous_send("ReadTimeout")
    return ToolOutput(success=False, error="request timed out")


async def definite_error() -> ToolOutput:
    return ToolOutput(success=False, error="404 project not found")


async def raises_unknown() -> ToolOutput:
    raise EffectUnknown("ack lost")


async def retried_and_succeeded() -> ToolOutput:
    note_ambiguous_send("ReadTimeout")
    return ToolOutput(success=True, data="created ISSUE-7")


async def raised_after_send() -> ToolOutput:
    note_ambiguous_send("RemoteProtocolError")
    raise RuntimeError("connection reset")


async def marks_in_a_child_task() -> ToolOutput:
    async def send() -> None:
        note_ambiguous_send("ReadTimeout")

    await asyncio.gather(send())
    return ToolOutput(success=False, error="timed out")


def executor(body: Callable[[], Awaitable[ToolOutput]]) -> tuple[ToolExecutor, ScriptedTool]:
    tool = ScriptedTool("create", body)
    registry = ToolRegistry()
    registry.register_tool(tool)
    return ToolExecutor(registry), tool


CALL = ToolCall(id="c1", name="create", arguments={})


@pytest.mark.parametrize(
    "body",
    [swallowed_timeout, raises_unknown, raised_after_send, marks_in_a_child_task],
    ids=["swallowed-maybe-sent", "raised-unknown", "raised-after-send", "am7-child-task"],
)
async def test_am5_a_failure_that_may_have_landed_raises_effect_unknown(
    body: Callable[[], Awaitable[ToolOutput]],
) -> None:
    subject, tool = executor(body)
    with pytest.raises(EffectUnknown):
        await subject.call_tool(CALL, unknown_effects=True)
    assert tool.calls == 1


async def test_am5_a_definite_error_stays_an_error() -> None:
    subject, _ = executor(definite_error)
    result = await subject.call_tool(CALL, unknown_effects=True)
    assert result.is_error and result.content == "404 project not found"


async def test_am5_a_success_after_an_ambiguous_attempt_is_ok() -> None:
    subject, _ = executor(retried_and_succeeded)
    result = await subject.call_tool(CALL, unknown_effects=True)
    assert not result.is_error and result.content == "created ISSUE-7"


@pytest.mark.parametrize(
    ("body", "content"),
    [
        (swallowed_timeout, "request timed out"),
        (raises_unknown, "ack lost"),
        (raised_after_send, "connection reset"),
    ],
)
async def test_am6_the_legacy_loop_keeps_error_results(
    body: Callable[[], Awaitable[ToolOutput]], content: str
) -> None:
    subject, _ = executor(body)
    result = await subject.call_tool(CALL)
    assert result.is_error and result.content == content
