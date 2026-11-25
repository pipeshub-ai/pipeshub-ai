"""AP-04 (C3.75): "Allow for this conversation" is approval for the session, scoped to a pattern of the call: the same
tool to the same destination. Later calls that match run unasked, in this run and in the conversation's next run;
a call to another destination, or another tool to the same one, still asks. The loop runs on the reference journal
with PipesHub's ledger and destinations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects.flow import GrantingLedger
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext

LOG = HarnessLog.named("tests.wp29")
SECRET = "Q3 acquisition target: Contoso"
NEXT_RUN = "run2"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


async def _fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
    return ToolExecution(status="ok", content=SECRET, events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


async def _share(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    _ = ctx
    return ToolExecution(status="ok", content={"shared": args.get("channel")})


def _world() -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    world.tools.add(FETCH_RECORD_TOOL_NAME, _fetch_record, tags=[("category", "read")])
    world.tools.add("share_message", _share, tags=[("category", "write")])
    world.admit()
    return world


def _kernel(world: World, run_id: str, *replies: object) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger())
    approval = flow_guarded(
        allow_all,
        ledger=ledger,
        run_id=run_id,
        log=LOG,
        rules=flow_rules(LOG),
        destination=pipeshub_destination,
        grants=ledger.grants,
    )
    return world.kernel(port, approval=approval, ledger=ledger)


READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))


def _call(tool: str, channel: str) -> object:
    return reply("", (tool, {"channel": channel, "text": SECRET}))


def _posted(world: World) -> list[object]:
    return [e.payload.get("channel") for e in world.saas.effects if e.op == "post_message"]


def _asked(world: World, run_id: str) -> list[tuple[str, str]]:
    return [
        (str(e.data["toolId"]).rsplit("/", 1)[-1], str(e.data["destination"]["label"]))  # type: ignore[index]
        for e in world.events("interaction.explained", run_id)
    ]


async def _granted_for_sales(world: World) -> None:
    assert (await world.run(_kernel(world, RUN, READ, _call("post_message", "sales")))).status == "suspended"
    [requested] = world.events("interaction.requested")
    [explained] = world.events("interaction.explained")
    assert explained.data["conversationGrant"] is True
    world.journal.resolve_interaction(RUN, str(requested.data["interactionId"]), "approved", scope="conversation")
    assert (await world.run(_kernel(world, RUN, _call("post_message", "sales"), reply("done")))).status == "completed"


async def test_ap04_matching_calls_run_unasked_and_others_still_ask_in_the_run() -> None:
    world = _world()
    assert (await world.run(_kernel(world, RUN, READ, _call("post_message", "sales")))).status == "suspended"
    [requested] = world.events("interaction.requested")
    world.journal.resolve_interaction(RUN, str(requested.data["interactionId"]), "approved", scope="conversation")
    resumed = _kernel(
        world, RUN, _call("post_message", "sales"), _call("post_message", "sales"), _call("share_message", "sales")
    )
    assert (await world.run(resumed)).status == "suspended"
    # The approved call, then the two that match the grant; the other tool waits.
    assert _posted(world) == ["sales", "sales", "sales"]
    assert _asked(world, RUN) == [("post_message", "#sales"), ("share_message", "#sales")]


async def test_ap04_the_grant_holds_for_the_conversations_next_run_and_only_for_its_pattern() -> None:
    world = _world()
    await _granted_for_sales(world)
    assert _posted(world) == ["sales", "sales"]
    world.admit(NEXT_RUN, text="post the summary again")
    later = _kernel(world, NEXT_RUN, READ, _call("post_message", "sales"), _call("post_message", "general"))
    assert (await world.run(later, NEXT_RUN)).status == "suspended"
    assert _posted(world) == ["sales", "sales", "sales"]
    assert _asked(world, NEXT_RUN) == [("post_message", "#general")]
