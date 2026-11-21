"""EffectRunner on the in-memory log (ER-1..ER-12)."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import (
    EffectCall,
    EffectKey,
    EffectRunner,
    RequestEffectLog,
    ToolEffect,
    ToolExecution,
    args_digest,
    tool_id_for,
)
from harness_core.journal.types import command_id
from harness_core.kernel.core.messages import ToolCall
from harness_core.testing.contracts.effect_log import (
    CountingTool,
    context,
    write_effect,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects import (
        ApprovalDecision,
        HoldDecision,
        RecordedEffect,
    )

RUN = "run-1"


def read_effect() -> ToolEffect:
    return ToolEffect(
        tool_id=tool_id_for("lookup"),
        effect="read",
        idempotent=True,
        sink=SinkDescriptor(egress="external", args_leave_boundary=True),
    )


def make_call(
    effect: ToolEffect,
    invocation: str = "inv-1",
    args: dict[str, JsonValue] | None = None,
) -> EffectCall:
    arguments: dict[str, JsonValue] = args if args is not None else {"title": "a"}
    return EffectCall(
        RUN,
        invocation,
        ToolCall(id=invocation, name="t", arguments=arguments),
        effect,
        args_digest(arguments),
    )


@dataclass
class SpyLog(RequestEffectLog):
    steps: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        super().__init__()

    @override
    async def intent(self, call: EffectCall) -> None:
        self.steps.append("intent")
        await super().intent(call)

    @override
    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None:
        self.steps.append(f"outcome:{execution.status}")
        await super().outcome(call, execution)

    @override
    async def unknown(self, call: EffectCall) -> None:
        self.steps.append("unknown")
        await super().unknown(call)

    @override
    async def refused(self, call: EffectCall, rule: str, reason: str) -> None:
        self.steps.append(f"refused:{rule}")
        await super().refused(call, rule, reason)

    @override
    async def find(self, key: EffectKey) -> RecordedEffect | None:
        self.steps.append("find")
        return await super().find(key)


@dataclass
class StepTool(CountingTool):
    log: SpyLog | None = None

    async def __call__(self, call: ToolCall, ctx: object) -> ToolExecution:  # type: ignore[override]
        if self.log is not None:
            self.log.steps.append("execute")
        return await super().__call__(call, ctx)  # type: ignore[arg-type]


def policy(decision: ApprovalDecision):  # noqa: ANN201
    def decide(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return decision

    return decide


@dataclass
class Hold:
    decision: HoldDecision | None
    held: list[str] = field(default_factory=list[str])

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        self.held.append(call.invocation_id)
        return self.decision


async def test_er1_intent_before_execute_and_outcome_after() -> None:
    log = SpyLog()
    tool = StepTool(log=log)
    call = make_call(write_effect())
    result = await EffectRunner(log).run(call, context(call), tool)
    assert result.disposition == "executed"
    assert log.steps == ["find", "intent", "execute", "outcome:ok"]


async def test_er2_policy_deny_records_a_refusal_and_does_not_execute() -> None:
    log, tool = SpyLog(), CountingTool()
    call = make_call(write_effect())
    result = await EffectRunner(log, policy("deny")).run(call, context(call), tool)
    assert result.disposition == "denied"
    assert result.execution == ToolExecution(status="error", content="denied by policy")
    assert log.steps == ["find", "refused:policy"]
    assert tool.calls == 0


async def test_er3_ask_without_a_hold_seam_is_held_with_nothing_recorded() -> None:
    log, tool = SpyLog(), CountingTool()
    call = make_call(write_effect())
    result = await EffectRunner(log, policy("ask")).run(call, context(call), tool)
    assert result.disposition == "held"
    assert log.steps == ["find"]
    assert await log.find(call.key) is None
    assert tool.calls == 0


@pytest.mark.parametrize(
    ("decision", "disposition", "calls", "steps"),
    [
        ("approved", "executed", 1, ["find", "intent", "outcome:ok"]),
        ("denied", "denied", 0, ["find", "refused:interaction_denied"]),
        (None, "held", 0, ["find"]),
    ],
)
async def test_er4_ask_with_a_hold_seam(
    decision: HoldDecision | None, disposition: str, calls: int, steps: list[str]
) -> None:
    log, tool, hold = SpyLog(), CountingTool(), Hold(decision)
    call = make_call(write_effect())
    result = await EffectRunner(log, policy("ask"), hold).run(call, context(call), tool)
    assert result.disposition == disposition
    assert hold.held == [call.invocation_id]
    assert tool.calls == calls
    assert log.steps == steps


async def test_er5_idempotent_write_is_retried_then_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = SpyLog()
    tool = CountingTool([ConnectionError("reset")])
    call = make_call(write_effect(idempotent=True))
    with caplog.at_level(logging.WARNING, logger="harness_core.effects"):
        result = await EffectRunner(log, max_retries=2).run(call, context(call), tool)
    assert result.disposition == "unknown"
    assert tool.calls == 3
    assert log.steps == ["find", "intent", "unknown"]
    events = [r.__dict__["harness_event"] for r in caplog.records]
    assert events == ["tool.retry", "tool.retry", "tool.unknown"]


async def test_er6_non_idempotent_write_is_not_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = SpyLog()
    tool = CountingTool([TimeoutError()])
    call = make_call(write_effect())
    with caplog.at_level(logging.WARNING, logger="harness_core.effects"):
        result = await EffectRunner(log).run(call, context(call), tool)
    assert result.disposition == "unknown"
    assert tool.calls == 1
    found = await log.find(call.key)
    assert found is not None and found.state == "unknown"
    (record,) = caplog.records
    assert record.__dict__["harness_event"] == "tool.unknown"
    assert record.__dict__["harness"] == {
        "invocationId": "inv-1",
        "toolId": call.effect.tool_id,
        "reason": "effect_may_have_happened",
        "errorType": "TimeoutError",
    }


async def test_er7_a_failing_read_is_an_error_outcome_and_reads_are_not_deduped() -> (
    None
):
    log = SpyLog()
    tool = CountingTool(
        [ValueError("bad query"), ToolExecution(status="ok", content="x")]
    )
    first = make_call(read_effect(), "inv-1")
    failed = await EffectRunner(log).run(first, context(first), tool)
    again = make_call(read_effect(), "inv-2")
    second = await EffectRunner(log).run(again, context(again), tool)
    assert failed.disposition == "executed"
    assert failed.execution == ToolExecution(
        status="error", content="ValueError: bad query", error_code="failed"
    )
    assert second.disposition == "executed"
    assert tool.calls == 2
    assert "find" not in log.steps


class _Fenced(Exception):
    pass


async def test_er8_propagated_exceptions_stop_without_an_outcome() -> None:
    log = SpyLog()
    tool = CountingTool([_Fenced()])
    call = make_call(write_effect(idempotent=True))
    with pytest.raises(_Fenced):
        _ = await EffectRunner(log, propagate=(_Fenced,)).run(call, context(call), tool)
    assert tool.calls == 1
    assert log.steps == ["find", "intent"]


async def test_er8_guard_runs_before_every_execution() -> None:
    log, tool = (
        SpyLog(),
        CountingTool([ConnectionError(), ToolExecution(status="ok", content=1)]),
    )
    guarded: list[int] = []
    call = make_call(write_effect(idempotent=True))
    runner = EffectRunner(log, guard=lambda: guarded.append(tool.calls))
    result = await runner.run(call, context(call), tool)
    assert result.disposition == "executed"
    assert guarded == [0, 1]


@pytest.mark.parametrize(
    ("idempotent", "disposition", "calls", "steps"),
    [
        (True, "executed", 1, ["find", "outcome:ok"]),
        (False, "unknown", 0, ["find", "unknown"]),
    ],
)
async def test_er9_dangling_intent_of_the_same_invocation(
    idempotent: bool, disposition: str, calls: int, steps: list[str]
) -> None:
    log, tool = SpyLog(), CountingTool()
    call = make_call(write_effect(idempotent=idempotent))
    await log.intent(call)
    log.steps.clear()
    result = await EffectRunner(log).run(call, context(call), tool)
    assert result.disposition == disposition
    assert tool.calls == calls
    assert log.steps == steps


async def test_er10_dangling_intent_of_another_invocation_is_in_flight() -> None:
    log, tool = SpyLog(), CountingTool()
    first = make_call(write_effect(idempotent=True), "inv-1")
    await log.intent(first)
    again = make_call(write_effect(idempotent=True), "inv-2")
    result = await EffectRunner(log).run(again, context(again), tool)
    assert (result.disposition, result.invocation_id) == ("in_flight", "inv-1")
    assert tool.calls == 0


async def test_er11_request_log_mirrors_ids_not_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = RequestEffectLog()
    call = make_call(write_effect(), args={"title": "secret title"})
    with caplog.at_level(logging.INFO, logger="harness_core.effects"):
        await log.intent(call)
        await log.outcome(call, ToolExecution(status="ok", content="secret result"))
        await log.unknown(call)
    assert [r.__dict__["harness_event"] for r in caplog.records] == [
        "effect.intent",
        "effect.outcome",
        "effect.unknown",
    ]
    for record in caplog.records:
        fields = record.__dict__["harness"]
        assert fields["runId"] == RUN and fields["invocationId"] == "inv-1"
        assert fields["toolId"] == call.effect.tool_id
        assert "secret" not in record.getMessage()


def test_er12_keys_and_command_ids_are_pure() -> None:
    a = make_call(write_effect(), "inv-1", {"b": 1, "a": [1, 2]})
    b = make_call(write_effect(), "inv-2", {"a": [1, 2], "b": 1})
    assert a.key == b.key == EffectKey(RUN, tool_id_for("create_issue"), a.args_digest)
    assert args_digest({"a": [1, 2], "b": 1}) == a.args_digest
    assert command_id("run-1.a2", "intent", 7) == "run-1.a2:intent:7"


def test_is1_effects_import_alone() -> None:
    """The effect layer sits below the kernel and never reaches the PipesHub app."""
    code = (
        "import sys, harness_core.effects, harness_core.testing.contracts.effect_log\n"
        "bad = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')"
        " or m.startswith('harness_core.kernel.durable'))\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
