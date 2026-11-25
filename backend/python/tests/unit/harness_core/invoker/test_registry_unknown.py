"""AM-8..AM-11: an ambiguous send through the real tool port is ``unknown`` and never runs again."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from harness_core.effects import EffectResult, EffectRunner, RequestEffectLog
from harness_core.invoker import InvocationScope, Invoker
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import DurableKernel
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.ids import SeededIds
from harness_core.tools.base import ToolOutput
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from tests.unit.harness_core.durable.support import World, reply, scripted
from tests.unit.harness_core.tools.test_executor_unknown import (
    ScriptedTool,
    swallowed_timeout,
)

if TYPE_CHECKING:
    from harness_core.effects import EffectKind
    from harness_core.invoker import InvokeResult

SCOPE = InvocationScope("run-1", "org-1", "run-1.a1")


def port(kind: EffectKind, *, idempotent: bool | None = None) -> tuple[RegistryToolPort, ScriptedTool]:
    tool = ScriptedTool("create", swallowed_timeout)
    registry = ToolRegistry()
    registry.register_tool(tool)
    effect = offered("create", kind).effect
    if idempotent is not None:
        effect = dataclasses.replace(effect, idempotent=idempotent)
    return RegistryToolPort(registry, {"create": effect}), tool


def subject(catalog: RegistryToolPort) -> Invoker:
    return Invoker(catalog, EffectRunner(RequestEffectLog(), max_retries=2), SCOPE, ids=SeededIds())


def executed(result: InvokeResult) -> EffectResult:
    assert isinstance(result, EffectResult), result
    return result


CALL = ToolCall(id="c1", name="create", arguments={})


async def test_am8_a_non_idempotent_write_is_unknown_and_never_runs_again() -> None:
    catalog, tool = port("write", idempotent=False)
    invoker = subject(catalog)
    first = executed(await invoker.invoke(CALL))
    again = executed(await invoker.invoke(CALL))
    assert (first.disposition, again.disposition) == ("unknown", "unknown")
    assert again.invocation_id == first.invocation_id
    assert tool.calls == 1


async def test_am9_an_idempotent_write_is_retried_then_unknown() -> None:
    catalog, tool = port("write", idempotent=True)
    result = executed(await subject(catalog).invoke(CALL))
    assert result.disposition == "unknown"
    assert tool.calls == 3


async def test_am10_a_read_is_an_error_never_unknown() -> None:
    catalog, tool = port("read")
    result = executed(await subject(catalog).invoke(CALL))
    assert result.disposition == "executed"
    assert result.execution is not None and result.execution.status == "error"
    assert tool.calls == 1


async def test_am10_a_definite_error_of_a_write_runs_again() -> None:
    async def not_found() -> ToolOutput:
        return ToolOutput(success=False, error="404")

    tool = ScriptedTool("create", not_found)
    registry = ToolRegistry()
    registry.register_tool(tool)
    invoker = subject(RegistryToolPort(registry, {"create": offered("create", "write").effect}))
    first = executed(await invoker.invoke(CALL))
    again = executed(await invoker.invoke(CALL))
    assert (first.disposition, again.disposition) == ("executed", "executed")
    assert tool.calls == 2


async def test_am11_the_durable_kernel_journals_unknown_and_suspends() -> None:
    world = World()
    world.admit()
    catalog, tool = port("write", idempotent=False)
    _, model = scripted(reply("", ("create", {})), reply("could not confirm"))
    kernel = DurableKernel(
        journal=world.journal,
        content=world.content,
        model=model,
        tools=catalog,
        clock=world.clock,
        checkpoints=world.checkpoints,
        live=world.live,
    )
    disposition = await world.run(kernel)
    assert disposition.status == "suspended"
    assert [e.type for e in world.events()][-3:] == [
        "tool.unknown",
        "interaction.requested",
        "run.suspended",
    ]
    assert world.events("tool.outcome") == []
    assert tool.calls == 1
