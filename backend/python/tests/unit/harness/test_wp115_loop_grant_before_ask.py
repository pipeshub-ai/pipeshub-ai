"""WP-115 (WP113-FU-1): a native loop run that is already running honours a conversation grant copied to it before it
raises its next ask.

Node writes a copy of a person's "allow for this conversation" on every open run of the conversation
(``ConversationGrantReach``, ADR-GRANT-01 D2) and answers the asks open then. A loop run decided its calls on the
ledger it last read: a copy that landed after that read and before the run journaled its next ask (here, while a
read of the same turn ran) was read only after the ask, so the person was asked once more. The loop now reads its
stream before it journals an ask that offers a grant, as the engine kernel does (D3).
"""

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
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import GrantingLedger
from harness_core.effects.types import ToolEffect
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolExecution
from harness_core.logs import HarnessLog
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext

LOG = HarnessLog.named("tests.wp115")
OUT = SinkDescriptor(egress="external", args_leave_boundary=True)
LOCAL = SinkDescriptor(egress="none", args_leave_boundary=False)
SEARCH = "dynamic__web_search"
SEARCH_EFFECT = ToolEffect("web@duckduckgo/web_search", "read", True, OUT)
SLOW_EFFECT = ToolEffect("pipeshub@skills/load_skill", "read", True, LOCAL)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


def _search_key() -> str:
    where = pipeshub_destination(ToolCall(id="c", name=SEARCH, arguments={"query": "q"}), SEARCH_EFFECT)
    assert where is not None
    return where.key


def _world(*, grant_while_reading: str | None) -> World:
    """``grant_while_reading``: the sensitivity of a grant another run of the conversation is given while this
    run's read of the same turn runs (Node's copy lands on this run's stream then)."""
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})

    async def fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
        return ToolExecution(status="ok", content="Q3 plan", events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))

    async def slow_read(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        if grant_while_reading is not None:
            world.journal.share_grant(
                RUN,
                "ix.approve.sibling",
                "run-sibling",
                {"toolId": SEARCH_EFFECT.tool_id, "key": _search_key(), "sensitivity": grant_while_reading},
            )
        return ToolExecution(status="ok", content={"name": args.get("name"), "body": "notes"})

    async def web(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"results": []})

    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    world.tools.add("load_skill", slow_read, effect=SLOW_EFFECT)
    world.tools.add(SEARCH, web, effect=SEARCH_EFFECT)
    world.admit()
    return world


def _kernel(world: World, *replies: object) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger())
    approval = flow_guarded(
        lambda call, effect: "allow",
        ledger=ledger,
        run_id=RUN,
        log=LOG,
        rules=flow_rules(LOG),
        destination=pipeshub_destination,
        grants=ledger.grants,
    )
    return world.kernel(port, approval=approval, ledger=ledger)


READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))
# One turn: a search the flow rule asks about (the run read a private record) beside a read that runs first.
SEARCH_BESIDE_READ = reply("", (SEARCH, {"query": "Q3 competitors"}), ("load_skill", {"name": "notes"}))


def _ran(world: World, name: str) -> int:
    return sum(1 for tool, _ in world.tools.executed if tool == name)


async def test_lg1_a_grant_copied_while_the_turn_ran_answers_the_ask_the_run_was_about_to_raise() -> None:
    world = _world(grant_while_reading="private")
    done = await world.run(_kernel(world, READ, SEARCH_BESIDE_READ, reply("done")))
    assert done.status == "completed"
    assert world.events("interaction.requested") == []
    assert world.events("interaction.explained") == []
    assert _ran(world, SEARCH) == 1


async def test_lg2_a_copy_given_over_public_data_never_answers_an_ask_over_private_data() -> None:
    world = _world(grant_while_reading="public")
    assert (await world.run(_kernel(world, READ, SEARCH_BESIDE_READ, reply("done")))).status == "suspended"
    [explained] = world.events("interaction.explained")
    assert explained.data["conversationGrant"] is True
    assert _ran(world, SEARCH) == 0


async def test_lg3_without_a_grant_the_run_asks_once_as_before() -> None:
    world = _world(grant_while_reading=None)
    assert (await world.run(_kernel(world, READ, SEARCH_BESIDE_READ, reply("done")))).status == "suspended"
    assert len(world.events("interaction.requested")) == 1
    assert _ran(world, "load_skill") == 1 and _ran(world, SEARCH) == 0
