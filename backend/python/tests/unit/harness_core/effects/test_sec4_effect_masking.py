"""SEC4-5, SEC4-6: what a call returns, journals and presents carries the mask for every value its boundary resolved."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.effects.log import RequestEffectLog
from harness_core.effects.runner import EffectRunner
from harness_core.effects.types import (
    EffectCall,
    ToolContext,
    ToolEffect,
    ToolExecution,
)
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.security.secrets import ResolvedSecrets
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing.canary import (
    Canaries,
    Surface,
    assert_no_canary,
    secret_canary,
)
from harness_core.testing.contracts.effect_log import write_effect
from harness_core.testing.ids import SeededIds
from tests.support.token_vector import EXAMPLES

if TYPE_CHECKING:
    from pydantic import JsonValue

IDS = SeededIds(4055)
SECRET = secret_canary(IDS.uuid4)
VENDOR = next(example for _, example, entry in EXAMPLES if entry.forbidden)
READ = ToolEffect(tool_id="jira@j1/search_issues", effect="read", idempotent=True, sink=write_effect().sink)


def _call(effect: ToolEffect, invocation: str) -> EffectCall:
    return EffectCall("run-1", invocation, ToolCall(id=invocation, name="jira__tool", arguments={"q": "open"}), effect, "sha256:x")


def _ctx(call: EffectCall) -> ToolContext:
    return ToolContext("run-1", "org-1", call.invocation_id, "run-1-attempt-1", call.invocation_id)


class _Connector:
    """A tool whose client resolves its record on first use, then puts the value wherever a tool can."""

    def __init__(self, resolved: ResolvedSecrets, outcome: str) -> None:
        self._resolved = resolved
        self._outcome = outcome

    async def __call__(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        self._resolved.add({"credentials": {"client_secret": SECRET}})
        if self._outcome == "raise":
            raise RuntimeError(f"401 for client {SECRET}")
        echoed: JsonValue = {"issues": [f"key {SECRET}"]}
        return ToolExecution(
            status="ok" if self._outcome == "ok" else "error",
            content=echoed if self._outcome == "ok" else f"denied: {SECRET} {VENDOR}",
            receipt={"request": f"POST /issue?client_secret={SECRET}"},
            events=(NewEvent("x.pipeshub.tool.result_presented", {"content": f"shown {SECRET}", SECRET: 1}, critical=False),),
            slots={"last": f"slot {SECRET}"},
            answer=f"answer {SECRET}",
            meta={"source": SECRET},
        )


def _runner(log: RequestEffectLog, resolved: ResolvedSecrets) -> EffectRunner:
    return EffectRunner(log, secrets=resolved)


def _surfaces(log: RequestEffectLog, execution: ToolExecution | None) -> list[Surface]:
    return [Surface("returned execution", execution), Surface("journaled outcome", log.outcomes())]


@pytest.mark.parametrize("outcome", ["ok", "error", "raise"])
async def test_sec4_5_a_read_masks_every_field_of_its_outcome(outcome: str) -> None:
    log, resolved = RequestEffectLog(), ResolvedSecrets()
    call = _call(READ, f"inv-{outcome}")

    result = await _runner(log, resolved).run(call, _ctx(call), _Connector(resolved, outcome))

    assert result.execution is not None
    assert SECRET_MASK in str(result.execution)
    # A failure's text carries a vendor shape too: masked by vector as well.
    assert_no_canary(_surfaces(log, result.execution), Canaries(secrets=(SECRET,)))


@pytest.mark.parametrize("outcome", ["ok", "error"])
async def test_sec4_5_a_write_masks_what_it_journals_and_returns(outcome: str) -> None:
    log, resolved = RequestEffectLog(), ResolvedSecrets()
    call = _call(write_effect(), f"inv-w-{outcome}")

    result = await _runner(log, resolved).run(call, _ctx(call), _Connector(resolved, outcome))

    assert result.disposition == "executed" and result.execution is not None
    execution = result.execution
    assert execution.receipt == {"request": f"POST /issue?client_secret={SECRET_MASK}"}
    assert execution.events[0].data == {"content": f"shown {SECRET_MASK}", SECRET_MASK: 1}
    assert (dict(execution.slots), execution.answer, execution.meta) == (
        {"last": f"slot {SECRET_MASK}"}, f"answer {SECRET_MASK}", {"source": SECRET_MASK}
    )
    assert_no_canary(_surfaces(log, execution), Canaries(secrets=(SECRET,)))


async def test_sec4_5_values_resolved_before_the_call_are_masked_and_none_outside_a_call() -> None:
    log, resolved = RequestEffectLog(), ResolvedSecrets()
    resolved.add({"token": SECRET})

    async def cached_client(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content=f"echo {SECRET}")

    call = _call(READ, "inv-cached")
    result = await EffectRunner(log, secrets=resolved).run(call, _ctx(call), cached_client)
    unscoped = await EffectRunner(RequestEffectLog()).run(call, _ctx(call), cached_client)

    assert result.execution is not None and result.execution.content == f"echo {SECRET_MASK}"
    assert unscoped.execution is not None and unscoped.execution.content == f"echo {SECRET}"
