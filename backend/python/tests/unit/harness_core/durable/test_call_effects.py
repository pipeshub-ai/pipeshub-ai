"""C-3: a tool whose effect depends on its arguments is classified per call, failing closed."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolExecution, classify_tool_effect
from harness_core.kernel.durable.effects import with_call_effect
from harness_core.logs import HarnessLog
from harness_core.tools.base import ParameterType, Tag, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder

from .support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pytest
    from pydantic import JsonValue

    from harness_core.kernel.durable import EffectKind, ToolContext


def _by_statement(arguments: Mapping[str, JsonValue]) -> EffectKind:
    statement = str(arguments.get("q", ""))
    if statement.startswith("SELECT"):
        return "read"
    if statement == "boom":
        raise ValueError("unparseable")
    return "write"


class _Crm:
    @tool(
        path="/tools/crm/query",
        short_description="Query",
        description="Run a model-written query.",
        parameters=[ToolParameter(name="q", type=ParameterType.STRING, description="Query", required=True)],
        tags=[Tag("effect", "write")],
    )
    async def query(self, q: str) -> str:
        return f"rows for {q}"


def _port() -> RegistryToolPort:
    registry = ToolRegistry()
    ToolsetBuilder(_Crm(), name="crm", description="CRM", path_prefix="/tools/crm").register_into(registry)
    effect = classify_tool_effect("crm__query", [Tag("effect", "write")])
    return RegistryToolPort(
        registry,
        {"crm__query": effect},
        call_effects={"crm__query": _by_statement},
        log=HarnessLog.named("tests.call_effects", runId="run1"),
    )


def test_c3_a_reading_call_is_read_a_writing_one_keeps_the_tool_class() -> None:
    port = _port()
    read = port.describe_call(ToolCall(id="c1", name="crm__query", arguments={"q": "SELECT Id FROM A"}))
    write = port.describe_call(ToolCall(id="c2", name="crm__query", arguments={"q": "DELETE A"}))
    assert read is not None and write is not None
    assert (read.effect, read.idempotent, read.needs_intent_commit) == ("read", True, False)
    assert (write.effect, write.idempotent, write.needs_intent_commit) == ("write", False, True)
    assert read.tool_id == write.tool_id
    # The name alone still describes the tool's own (fail-closed) class.
    assert port.describe("crm__query") == write


def test_c3_a_doubled_name_is_classified_as_the_tool_it_resolves_to() -> None:
    port = _port()
    effect = port.describe_call(ToolCall(id="c1", name="crm__querycrm__query", arguments={"q": "SELECT 1"}))
    assert effect is not None and effect.effect == "read"


def test_c3_a_failing_classifier_leaves_the_call_a_write_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    port = _port()
    with caplog.at_level(logging.WARNING, logger="tests.call_effects"):
        effect = port.describe_call(ToolCall(id="c1", name="crm__query", arguments={"q": "boom"}))
    assert effect is not None and effect.effect == "write"
    [record] = [r for r in caplog.records if "tool.call_effect_failed" in r.getMessage()]
    assert "runId=run1" in record.getMessage() and "boom" not in record.getMessage()


def test_c3_a_write_keeps_a_writing_tool_idempotency_never_a_reading_tool() -> None:
    idempotent_write = classify_tool_effect("x", [Tag("effect", "write"), Tag("idempotent", "true")])
    assert with_call_effect(idempotent_write, "read").idempotent
    assert with_call_effect(with_call_effect(idempotent_write, "read"), "write").idempotent is False
    read = classify_tool_effect("y", [Tag("effect", "read")])
    assert with_call_effect(read, "write").idempotent is False


async def test_c3_the_kernel_commits_an_intent_only_for_the_writing_call() -> None:
    world = World()
    world.admit()

    async def query(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"rows": 1})

    world.tools.add("crm_query", query, tags=[("effect", "write")], call_effect=_by_statement)
    _, port = scripted(
        reply("", ("crm_query", {"q": "SELECT Id FROM A"})),
        reply("", ("crm_query", {"q": "UPDATE A"})),
        reply("done"),
    )
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    # A read journals its intent with its outcome; a write commits it first, alone.
    intents = world.events("tool.intent")
    assert [(e.data["invocationId"], e.data["effect"]) for e in intents] == [("t1.0", "read"), ("t2.0", "write")]
    assert [e.data["status"] for e in world.events("tool.outcome")] == ["ok", "ok"]


async def test_c2_a_call_to_a_tool_the_run_lacks_is_refused_by_the_port() -> None:
    """The kernel hands an unknown call to the port (whose hooks legacy runs
    around it) and journals the port's refusal; it never plans it as an effect."""
    world = World()
    world.admit()
    seen: list[str] = []
    real = world.tools.execute

    async def execute(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        seen.append(call.name)
        return await real(call, ctx)

    world.tools.execute = execute  # type: ignore[method-assign]
    _, port = scripted(reply("", ("ghost_tool", {})), reply("done"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert seen == ["ghost_tool"]
    [outcome] = world.events("tool.outcome")
    assert outcome.data["status"] == "error"
    [intent] = world.events("tool.intent")
    assert intent.data["effect"] == "write" and intent.data["invocationId"] == "t1.0"
