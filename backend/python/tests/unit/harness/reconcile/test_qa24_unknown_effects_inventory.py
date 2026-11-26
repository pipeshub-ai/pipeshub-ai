"""QA24-3, QA24-4 (APR-G7): every write a durable run can reach either has a reconcile hook or parks for a person.

Two tools have hooks (``HOOKS``); every other non-idempotent ``write`` or ``execute`` tool is park-only by default.
The inventory is the set of tools P2.1's enumerators build, the way their sources do, so a new connector's writes are
in it the day they ship. Each one runs on the durable kernel under its own name and tags, its effect lands and then
its worker dies (or its acknowledgement is lost): a hooked tool is decided by its read, any other parks on
``ix.unknown`` and is never executed again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from app.harness.reconcile import HOOKS
from harness_core.effects.reconcile import TimedReconciler, current_effect_tag
from harness_core.failpoints import Raise
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.testing import armed
from tests.unit.harness.test_effect_tags_complete import ENUMERATORS
from tests.unit.harness_core.durable.support import (
    RUN,
    Crash,
    NoteLookup,
    World,
    reply,
    scripted,
    stepping,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import ToolContext
    from harness_core.tools.base import Tool

UNKNOWN = "ix.unknown.t1.0"
Fault = Literal["crash_after_effect", "lost_ack"]


# Reviewed: a write declared idempotent is retried after a lost acknowledgement with ``ctx.idempotency_key``, which is
# safe only if the tool sends that key and its provider dedupes on it. Each entry names where it does.
IDEMPOTENT_WRITES: dict[str, str] = {}


@pytest.fixture(scope="module")
def reachable() -> dict[str, Tool]:
    return {tool.name: tool for enumerate_ in ENUMERATORS.values() for tool in enumerate_()}


@pytest.fixture(scope="module")
def writes(reachable: dict[str, Tool]) -> dict[str, Tool]:
    """Every non-idempotent ``write``/``execute`` tool a durable run can reach, by name."""
    return {
        name: tool
        for name, tool in reachable.items()
        if (effect := classify_tool_effect(name, tool.tags)).effect in ("write", "execute") and not effect.idempotent
    }


def test_qa24_3_no_write_is_retried_on_a_key_nobody_reviewed(reachable: dict[str, Tool]) -> None:
    keyed = {
        name
        for name, tool in reachable.items()
        if (effect := classify_tool_effect(name, tool.tags)).effect in ("write", "execute") and effect.idempotent
    }
    assert keyed == set(IDEMPOTENT_WRITES), "a write tagged idempotent=true is re-sent after a lost ack: review it"


def test_qa24_3_every_hook_names_a_reachable_non_idempotent_write(writes: dict[str, Tool]) -> None:
    stale = sorted(set(HOOKS) - set(writes))
    assert stale == [], f"hooks whose tool no durable run reaches as a non-idempotent write (renamed?): {stale}"


def test_qa24_3_the_inventory_spans_every_write_family(writes: dict[str, Tool]) -> None:
    """A floor, so an enumerator that silently lost a source cannot make QA24-4 pass over nothing."""
    families = {name.split("__", 1)[0] for name in writes if "__" in name}
    assert {"jira", "slack", "gmail", "outlook", "teams", "github", "salesforce", "confluence"} <= families
    assert any(name.startswith("mcp_") for name in writes)
    assert any(classify_tool_effect(n, t.tags).effect == "execute" for n, t in writes.items())


async def _recover(tool: Tool, fault: Fault) -> tuple[str, int, int, list[str]]:
    """(status, executions, provider effects, reconcile verdicts) after the fault and the next attempt."""
    world = World()
    world.admit()

    async def write(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        landed = world.call_saas(tool.name, {**args, "tag": current_effect_tag() or ""}, None)
        return ToolExecution(status="ok", content={"id": landed.receipt_id})

    world.tools.add(tool.name, write, tags=[(t.key, t.value) for t in tool.tags])
    effect = world.tools.describe(tool.name)
    assert effect is not None
    hooks = {effect.tool_id: NoteLookup(world, op=tool.name)} if tool.name in HOOKS else {}
    _, port = scripted(reply("", (tool.name, {"q": "x"})), reply("done"))

    def kernel():  # noqa: ANN202 - the kernel each attempt builds
        reconciler = TimedReconciler(hooks, world.clock, stepping(world), retry_s=1.0)
        return world.kernel(port, reconciler=reconciler, renew_interval_s=5.0, sleep=world.clock.sleep)

    if fault == "crash_after_effect":
        with armed({"worker.tool.after_effect": Raise(Crash)}):
            await world.crash(kernel())
    else:
        world.lose_ack_calls = 1
    done = await world.run(kernel())
    status = done.status if done.status != "suspended" else f"suspended:{done.reason}"
    verdicts = [str(e.data["verdict"]) for e in world.events("tool.reconciled", run_id=RUN)]
    return status, len(world.tools.executed), len(world.saas.effects), verdicts


@pytest.mark.parametrize("fault", ["crash_after_effect", "lost_ack"])
async def test_qa24_4_every_write_is_reconciled_by_its_hook_or_parks_for_a_person(
    writes: dict[str, Tool], fault: Fault
) -> None:
    hooked = ("completed", 1, 1, ["applied"])
    parked: tuple[str, int, int, list[str]] = (f"suspended:{UNKNOWN}", 1, 1, [])
    wrong: dict[str, tuple[str, int, int, list[str]]] = {}
    for name, tool in sorted(writes.items()):
        got = await _recover(tool, fault)
        if got != (hooked if name in HOOKS else parked):
            wrong[name] = got
    assert wrong == {}, f"(status, executions, effects, verdicts) off the documented recovery: {wrong}"
