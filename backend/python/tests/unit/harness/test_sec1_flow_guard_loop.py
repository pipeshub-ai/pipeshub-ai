"""SEC1 FL-9: the lethal trifecta on the durable loop (the in-product agent), whose approvals allow every
write by default (P2.6c). The kernel feeds PipesHub's session ledger every event it folds, and the flow
rule narrows an external write once the run has read a record."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects.flow import FLOW_RULE
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext

SECRET = "Q3 acquisition target: Contoso"


async def fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE"}}}
    return ToolExecution(status="ok", content=SECRET, events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


def _world() -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    world.admit()
    return world


def _kernel(world: World, *replies: object) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = SessionSourceLedger()
    log = HarnessLog.named("tests.sec1")
    approval = flow_guarded(allow_all, rules=flow_rules(log), ledger=ledger, run_id=RUN, log=log)
    return world.kernel(port, approval=approval, ledger=ledger)


def _posts(world: World) -> int:
    return sum(1 for e in world.saas.effects if e.op == "post_message")


def _rules(world: World) -> list[object]:
    return [e.data.get("rule") for e in world.events() if e.type == "tool.denied"]


READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))
WRITE = reply("", ("post_message", {"text": SECRET}))


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "false")
    monkeypatch.delenv(FLOW_LEVEL_ENV, raising=False)


async def test_fl9_off_the_loop_posts_as_today() -> None:
    world = _world()
    assert (await world.run(_kernel(world, READ, WRITE, reply("done")))).status == "completed"
    assert _posts(world) == 1


async def test_fl9_deny_the_post_after_the_read_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    monkeypatch.setenv(FLOW_LEVEL_ENV, "deny")
    world = _world()
    assert (await world.run(_kernel(world, READ, WRITE, reply("could not post")))).status == "completed"
    assert _posts(world) == 0
    assert _rules(world) == [FLOW_RULE]


async def test_fl9_ask_the_post_after_the_read_suspends_for_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    world = _world()
    done = await world.run(_kernel(world, READ, WRITE, reply("posted")))
    assert done.status == "suspended"
    assert "interaction.requested" in world.types()
    assert _posts(world) == 0


async def test_fl9_a_post_before_any_read_is_not_narrowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    monkeypatch.setenv(FLOW_LEVEL_ENV, "deny")
    world = _world()
    assert (await world.run(_kernel(world, reply("", ("post_message", {"text": "hi"})), reply("ok")))).status == "completed"
    assert _posts(world) == 1


async def test_fl9_a_read_and_a_post_in_one_batch_are_judged_on_what_the_model_had_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post's arguments were written before the read's result existed: its context was public."""
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    monkeypatch.setenv(FLOW_LEVEL_ENV, "deny")
    world = _world()
    both = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}), ("post_message", {"text": "hi"}))
    assert (await world.run(_kernel(world, both, reply("ok")))).status == "completed"
    assert _posts(world) == 1


async def test_fl8_a_later_attempt_restored_from_a_checkpoint_still_knows_the_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new attempt's fold starts at the checkpoint; the ledger must still see the read before it."""
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    monkeypatch.setenv(FLOW_LEVEL_ENV, "deny")
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    world.admit()
    first = _kernel(world, READ, reply("unused"))

    async def read_then_drain(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        first.request_drain()
        return await fetch_record(args, ctx)

    world.tools.add(FETCH_RECORD_TOOL_NAME, read_then_drain, tags=[("category", "read")])
    assert (await world.run(first)).status == "yielded"
    claim = await world.claim()
    assert claim.checkpoint_ref is not None, "the later attempt must start from a checkpoint"
    second = await _kernel(world, WRITE, reply("could not post")).run_claimed(claim)
    assert second.status == "completed"
    assert _posts(world) == 0
    assert _rules(world) == [FLOW_RULE]
