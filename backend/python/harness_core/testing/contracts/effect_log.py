"""Contract suite every ``EffectLog`` adapter must pass, with the runner's dedupe over it.

Subclass :class:`EffectLogContract` in a test module and provide a ``harness``
fixture returning an :class:`EffectLogHarness`: the log under test and a way
to start a new invocation in its run (a journal-backed log, for example, must
journal the model response that asked for the call first). The in-memory and
journal adapters run it in ``tests/unit/harness_core/effects``; another host
(a Loom-backed log, say) runs it against its own adapter the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.runner import EffectRunner
from harness_core.effects.types import (
    EffectKey,
    EffectUnknown,
    ToolContext,
    ToolEffect,
    ToolExecution,
    tool_id_for,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.log import EffectLog
    from harness_core.effects.types import EffectCall
    from harness_core.kernel.core.messages import ToolCall


class EffectLogHarness(Protocol):
    @property
    def log(self) -> EffectLog: ...

    async def new_call(
        self, effect: ToolEffect, args: Mapping[str, JsonValue]
    ) -> EffectCall:
        """A new invocation, in the run under test, of the tool ``effect`` describes."""
        ...


def write_effect(name: str = "create_issue", *, idempotent: bool = False) -> ToolEffect:
    return ToolEffect(
        tool_id=tool_id_for(name),
        effect="write",
        idempotent=idempotent,
        sink=SinkDescriptor(egress="external", args_leave_boundary=True),
    )


def context(call: EffectCall) -> ToolContext:
    return ToolContext(
        call.run_id,
        "scope-1",
        call.invocation_id,
        "attempt-1",
        f"key:{call.invocation_id}",
    )


@dataclass
class CountingTool:
    """A tool double: answers from ``replies`` in order (an exception is raised)."""

    replies: list[ToolExecution | Exception] = field(
        default_factory=list[ToolExecution | Exception]
    )
    calls: int = 0

    async def __call__(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        self.calls += 1
        reply = (
            self.replies[min(self.calls, len(self.replies)) - 1]
            if self.replies
            else None
        )
        if isinstance(reply, Exception):
            raise reply
        return reply or ToolExecution(
            status="ok",
            content={"created": ctx.invocation_id},
            receipt={"id": ctx.invocation_id},
        )


class EffectLogContract:
    @pytest.fixture
    def harness(self) -> EffectLogHarness:
        raise NotImplementedError("provide a harness fixture")

    async def test_el1_nothing_recorded(self, harness: EffectLogHarness) -> None:
        call = await harness.new_call(write_effect(), {"title": "a"})
        assert await harness.log.find(call.key) is None

    async def test_el2_intent_is_findable(self, harness: EffectLogHarness) -> None:
        call = await harness.new_call(write_effect(), {"title": "a"})
        await harness.log.intent(call)
        found = await harness.log.find(call.key)
        assert found is not None
        assert (found.state, found.invocation_id, found.key) == (
            "intent",
            call.invocation_id,
            call.key,
        )
        assert found.execution is None

    async def test_el3_outcome_is_recorded(self, harness: EffectLogHarness) -> None:
        call = await harness.new_call(write_effect(), {"title": "a"})
        await harness.log.intent(call)
        await harness.log.outcome(
            call,
            ToolExecution(status="ok", content={"issue": "J-1"}, receipt={"id": "J-1"}),
        )
        found = await harness.log.find(call.key)
        assert found is not None and found.state == "completed"
        assert found.execution is not None
        assert found.execution.status == "ok"
        assert found.execution.content == {"issue": "J-1"}
        assert found.execution.receipt == {"id": "J-1"}

    async def test_el4_unknown_is_recorded(self, harness: EffectLogHarness) -> None:
        call = await harness.new_call(write_effect(), {"title": "a"})
        await harness.log.intent(call)
        await harness.log.unknown(call)
        found = await harness.log.find(call.key)
        assert found is not None and found.state == "unknown"
        assert found.invocation_id == call.invocation_id

    async def test_el5_keys_are_isolated(self, harness: EffectLogHarness) -> None:
        call = await harness.new_call(write_effect(), {"title": "a"})
        await harness.log.intent(call)
        other_args = EffectKey(call.run_id, call.effect.tool_id, "sha256:" + "0" * 64)
        other_tool = EffectKey(
            call.run_id, tool_id_for("post_message"), call.args_digest
        )
        other_run = EffectKey(
            f"{call.run_id}-other", call.effect.tool_id, call.args_digest
        )
        assert await harness.log.find(other_args) is None
        assert await harness.log.find(other_tool) is None
        assert await harness.log.find(other_run) is None

    async def test_el6_latest_invocation_answers(
        self, harness: EffectLogHarness
    ) -> None:
        first = await harness.new_call(write_effect(), {"title": "a"})
        await harness.log.intent(first)
        await harness.log.outcome(first, ToolExecution(status="ok", content="one"))
        second = await harness.new_call(write_effect(), {"title": "a"})
        assert second.key == first.key and second.invocation_id != first.invocation_id
        await harness.log.intent(second)
        found = await harness.log.find(second.key)
        assert found is not None
        assert (found.state, found.invocation_id) == ("intent", second.invocation_id)

    async def test_el7_a_refusal_is_not_findable(
        self, harness: EffectLogHarness
    ) -> None:
        call = await harness.new_call(write_effect(), {"title": "a"})
        await harness.log.refused(call, "policy", "denied by policy")
        assert await harness.log.find(call.key) is None

    async def test_ed1_identical_recall_returns_the_recorded_outcome(
        self, harness: EffectLogHarness
    ) -> None:
        runner, tool = EffectRunner(harness.log), CountingTool()
        first = await harness.new_call(write_effect(), {"title": "a"})
        done = await runner.run(first, context(first), tool)
        again = await harness.new_call(write_effect(), {"title": "a"})
        replayed = await runner.run(again, context(again), tool)
        assert done.disposition == "executed"
        assert replayed.disposition == "recorded"
        assert replayed.invocation_id == first.invocation_id
        assert replayed.execution is not None and done.execution is not None
        assert replayed.execution.content == done.execution.content
        assert replayed.execution.receipt == done.execution.receipt
        assert tool.calls == 1

    async def test_ed2_recall_with_other_args_executes(
        self, harness: EffectLogHarness
    ) -> None:
        runner, tool = EffectRunner(harness.log), CountingTool()
        first = await harness.new_call(write_effect(), {"title": "a"})
        _ = await runner.run(first, context(first), tool)
        other = await harness.new_call(write_effect(), {"title": "b"})
        result = await runner.run(other, context(other), tool)
        assert result.disposition == "executed"
        assert result.invocation_id == other.invocation_id
        assert tool.calls == 2
        found = await harness.log.find(other.key)
        assert found is not None and found.invocation_id == other.invocation_id

    async def test_ed3_recall_after_unknown_is_not_executed(
        self, harness: EffectLogHarness
    ) -> None:
        runner = EffectRunner(harness.log)
        tool = CountingTool([EffectUnknown("ack lost")])
        first = await harness.new_call(write_effect(), {"title": "a"})
        lost = await runner.run(first, context(first), tool)
        again = await harness.new_call(write_effect(), {"title": "a"})
        result = await runner.run(again, context(again), tool)
        assert lost.disposition == "unknown"
        assert result.disposition == "unknown"
        assert result.invocation_id == first.invocation_id
        assert tool.calls == 1

    async def test_ed4_recall_after_an_error_executes(
        self, harness: EffectLogHarness
    ) -> None:
        runner = EffectRunner(harness.log)
        tool = CountingTool(
            [
                ToolExecution(status="error", content="rate limited"),
                ToolExecution(status="ok", content="J-2"),
            ]
        )
        first = await harness.new_call(write_effect(), {"title": "a"})
        failed = await runner.run(first, context(first), tool)
        again = await harness.new_call(write_effect(), {"title": "a"})
        result = await runner.run(again, context(again), tool)
        assert failed.disposition == "executed"
        assert result.disposition == "executed"
        assert result.execution is not None and result.execution.content == "J-2"
        assert tool.calls == 2


__all__ = [
    "CountingTool",
    "EffectLogContract",
    "EffectLogHarness",
    "context",
    "write_effect",
]
