"""UX1: the flow guard is on by default for chat, in ``ask`` mode (FG), the worker says why it asks (AR-6), a
person's "allow for this conversation" holds for one destination (CG), and a denial is a refusal the model can
explain (DN-1). The durable loop runs on the reference journal with PipesHub's ledger and destinations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.control_plane.runtime.deployment import flow_guard_enabled
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects.flow import FLOW_RULE, FlowRule, GrantingLedger
from harness_core.effects.policy import LayerFlow
from harness_core.effects.types import APPROVAL_DENIAL
from harness_core.journal import ConversationGrantNotOffered
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.durable import DurableKernel, ToolContext

LOG = HarnessLog.named("tests.ux1")
SECRET = "Q3 acquisition target: Contoso"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


async def fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
    return ToolExecution(status="ok", content=SECRET, events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


def _world() -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    world.admit()
    return world


def _kernel(world: World, *replies: object, flows: tuple[LayerFlow, ...] = ()) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger())
    approval = flow_guarded(
        allow_all,
        ledger=ledger,
        run_id=RUN,
        log=LOG,
        rules=flow_rules(LOG, flows),
        destination=pipeshub_destination,
        grants=ledger.grants,
    )
    return world.kernel(port, approval=approval, ledger=ledger)


def _posts(world: World) -> list[object]:
    return [e.payload.get("channel") for e in world.saas.effects if e.op == "post_message"]


def _explained(world: World) -> list[JournalEvent]:
    return world.events("interaction.explained")


def _open_ix(world: World) -> str:
    requested = world.events("interaction.requested")
    assert requested, "expected a held call"
    return str(requested[-1].data["interactionId"])


READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))


def post(channel: str) -> object:
    return reply("", ("post_message", {"channel": channel, "text": SECRET}))


# FG-1


def test_fg1_the_deployment_rule_is_on_and_asks_by_default() -> None:
    assert flow_guard_enabled() is True
    assert flow_rules(LOG) == (FlowRule("ask", "public"),)


# FG-2


def test_fg2_the_off_switch_leaves_only_an_orgs_own_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "false")
    assert flow_rules(LOG) == ()
    assert flow_rules(LOG, (LayerFlow("ask", "public"),)) == (FlowRule("ask", "public"),)


# FG-3


def test_fg3_an_org_can_narrow_the_default_and_never_turn_it_off() -> None:
    assert flow_rules(LOG, (LayerFlow("deny", "public"),)) == (FlowRule("deny", "public"),)
    assert flow_rules(LOG, (LayerFlow("off", "public"),)) == (FlowRule("ask", "public"),)


# FG-5, AR-6


async def test_fg5_with_every_flag_at_its_default_a_post_after_a_read_waits_and_says_why() -> None:
    world = _world()
    done = await world.run(_kernel(world, READ, post("sales"), reply("posted")))
    assert done.status == "suspended"
    assert _posts(world) == []
    [explained] = _explained(world)
    assert explained.critical is False
    data = explained.data
    assert data["interactionId"] == _open_ix(world)
    assert data["rule"] == FLOW_RULE
    assert data["sensitivity"] == "private"
    assert data["sources"] == [{"title": "Q3 plan", "recordId": "rec-1"}]
    assert data["destination"]["label"] == "#sales"  # type: ignore[index]
    assert data["conversationGrant"] is True
    commits = world.journal.commits(RUN)
    assert any("interaction.requested" in types and "interaction.explained" in types for _, types in commits)


# FG-6


async def test_fg6_a_post_with_nothing_read_runs_without_asking() -> None:
    world = _world()
    assert (await world.run(_kernel(world, post("sales"), reply("ok")))).status == "completed"
    assert _posts(world) == ["sales"]
    assert _explained(world) == []


# AR-5: a grant is offered only when nothing but the flow rule holds the call.


async def test_ar5_no_grant_when_the_org_also_asks_for_writes() -> None:
    from harness_core.effects.policy import WRITES_ASK, LayeredApprovalPolicy

    world = _world()
    _, port = scripted(READ, post("sales"), reply("posted"))  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger())
    approval = flow_guarded(
        LayeredApprovalPolicy((WRITES_ASK,)),
        rules=flow_rules(LOG),
        ledger=ledger,
        run_id=RUN,
        log=LOG,
        destination=pipeshub_destination,
        grants=ledger.grants,
    )
    assert (await world.run(world.kernel(port, approval=approval, ledger=ledger))).status == "suspended"
    [explained] = _explained(world)
    assert explained.data["conversationGrant"] is False


# DN-1


async def test_dn1_a_person_declines_and_the_model_reads_a_refusal_it_can_explain() -> None:
    world = _world()
    assert (await world.run(_kernel(world, READ, post("sales"), reply("I did not post it")))).status == "suspended"
    world.journal.resolve_interaction(RUN, _open_ix(world), "denied")
    # A resumed attempt replays the journaled turns; its model is asked only for the next one.
    assert (await world.run(_kernel(world, reply("I did not post it")))).status == "completed"
    assert _posts(world) == []
    assert [e.data.get("rule") for e in world.events("tool.denied")] == [APPROVAL_DENIAL[0]]
    outcome = world.events("tool.outcome")[-1]
    assert outcome.data["status"] == "error"
    assert APPROVAL_DENIAL[1].startswith("approval_denied:")


# CG-1 (reference journal), CG-3, CG-4


async def test_cg3_allowed_for_the_conversation_the_same_channel_posts_and_another_asks() -> None:
    world = _world()
    assert (await world.run(_kernel(world, READ, post("sales")))).status == "suspended"
    world.journal.resolve_interaction(RUN, _open_ix(world), "approved", scope="conversation")
    assert [e.data["scope"] for e in world.events("interaction.granted")] == ["conversation"]
    resumed = _kernel(world, post("sales"), post("general"), reply("done"))
    assert (await world.run(resumed)).status == "suspended"
    assert _posts(world) == ["sales", "sales"]
    explained = _explained(world)
    assert [e.data["destination"]["label"] for e in explained] == ["#sales", "#general"]  # type: ignore[index]


async def test_cg2_a_grant_the_worker_did_not_offer_is_refused_by_the_journal() -> None:
    world = _world()
    assert (await world.run(_kernel(world, READ, reply("", ("post_message", {"text": SECRET})), reply("x")))).status == "suspended"
    [explained] = _explained(world)
    assert explained.data["conversationGrant"] is False
    with pytest.raises(ConversationGrantNotOffered):
        world.journal.resolve_interaction(RUN, _open_ix(world), "approved", scope="conversation")
    with pytest.raises(ConversationGrantNotOffered):
        world.journal.resolve_interaction(RUN, _open_ix(world), "denied", scope="conversation")
    assert world.events("interaction.granted") == []


async def test_cg_approved_once_the_next_post_to_the_same_channel_asks_again() -> None:
    world = _world()
    assert (await world.run(_kernel(world, READ, post("sales")))).status == "suspended"
    world.journal.resolve_interaction(RUN, _open_ix(world), "approved")
    assert (await world.run(_kernel(world, post("sales"), reply("done")))).status == "suspended"
    assert _posts(world) == ["sales"]
