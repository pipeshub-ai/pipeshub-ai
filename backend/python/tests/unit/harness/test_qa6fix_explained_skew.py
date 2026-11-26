"""QA6-F6 on the journal: a call held by an org's own rule is explained in the same commit as its request, by the
durable loop and by an engine run, and a worker ahead of its Node (a claim that does not name
``approval_rule_layers``) writes only what that Node's schema accepts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import approval_policy, engine_guarded, flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects import ToolExecution
from harness_core.effects.flow import ENGINE_WRITES_RULE, FLOW_RULE, GrantingLedger
from harness_core.effects.policy import LayeredApprovalPolicy
from harness_core.journal.types import APPROVAL_REASONS, APPROVAL_RULE_LAYERS, NewEvent
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.durable import CREATE, EngineWorld
from tests.unit.harness_core.durable.support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalPolicy
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import ToolContext
    from harness_core.security.labels import SourceLedger

LOG = HarnessLog.named("tests.qa6fix")
ORG = "org-1"
RUN = "run-f6"
OLDER_NODE = frozenset({APPROVAL_REASONS})
FETCH = offered(FETCH_RECORD_TOOL_NAME)
ENGINE_TOOLS = (FETCH, CREATE)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


def org_asks() -> LayeredApprovalPolicy:
    return approval_policy({"default": {"writes": "ask"}}, org_id=ORG, agent_key=None, log=LOG)


def cited() -> NewEvent:
    records = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Board notes"}}}
    return NewEvent(CITATIONS_EVENT, records, critical=False)


# The durable loop


async def loop_fetch(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content="board notes", events=(cited(),))


def loop_world(**journal: object) -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})}, **journal)  # type: ignore[arg-type]
    world.tools.add(FETCH_RECORD_TOOL_NAME, loop_fetch, tags=[("category", "read")])
    world.admit()
    return world


POST = reply("", ("post_message", {"channel": "sales", "text": "hello"}))
READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))


async def held(world: World, approval: ApprovalPolicy, *turns: object, ledger: GrantingLedger | None = None) -> list[JournalEvent]:
    _, port = scripted(*turns, reply("posted"))  # type: ignore[arg-type]
    done = await world.run(world.kernel(port, approval=approval, ledger=ledger))
    assert done.status == "suspended"
    assert len(world.events("interaction.requested")) == 1
    return world.events("interaction.explained")


async def test_f6_1_the_loop_explains_an_org_rule_in_the_commit_of_its_request() -> None:
    world = loop_world()
    [reason] = await held(world, org_asks(), POST)
    [request] = world.events("interaction.requested")
    assert reason.data["interactionId"] == request.data["interactionId"]
    assert {k: reason.data[k] for k in ("rule", "layer", "conversationGrant")} == {"rule": "writes", "layer": "deployment", "conversationGrant": False}
    # AP-06: a write the person may edit before approving says so.
    # R38-AUTHZ: with the call's arguments digest, which a decline covers.
    assert set(reason.data) == {"interactionId", "rule", "layer", "toolId", "conversationGrant", "editable", "argsDigest"}
    assert reason.critical is False and reason.writer == "worker" and reason.seq + 1 == request.seq


async def test_f6_6_an_older_node_gets_no_reason_for_an_org_rule_and_the_ask_still_stands(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    world = loop_world(journal_accepts=OLDER_NODE)
    assert await held(world, org_asks(), POST) == []
    assert "tool.approval_reason_withheld" in caplog.text


async def test_f6_6_an_older_node_gets_the_flow_reason_as_it_knows_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")

    async def flow_reason(**journal: object) -> Mapping[str, JsonValue]:
        world = loop_world(**journal)
        ledger = GrantingLedger(SessionSourceLedger())
        approval = flow_guarded(
            LayeredApprovalPolicy(),
            rules=flow_rules(LOG),
            ledger=ledger,
            run_id="run-1",
            log=LOG,
            destination=pipeshub_destination,
            grants=ledger.grants,
        )
        [reason] = await held(world, approval, READ, POST, ledger=ledger)
        return reason.data

    older = await flow_reason(journal_accepts=OLDER_NODE)
    current = await flow_reason()
    assert older["rule"] == FLOW_RULE and "layer" not in older
    # The current Node also takes an edited approval (AP-06); the older one is never offered it.
    assert current == {**older, "layer": "flow", "editable": True, "argsDigest": current["argsDigest"]}
    assert "editable" not in older and "argsDigest" not in older


async def test_f6_6_a_node_that_names_layers_is_told_them() -> None:
    world = loop_world(journal_accepts=OLDER_NODE | {APPROVAL_RULE_LAYERS})
    [reason] = await held(world, org_asks(), POST)
    assert (reason.data["rule"], reason.data["layer"]) == ("writes", "deployment")


async def test_f6_6_a_node_older_than_reasons_gets_none_of_any_rule() -> None:
    world = loop_world(journal_accepts=frozenset(), journal_refuses=frozenset({"interaction.explained"}))
    assert await held(world, org_asks(), POST) == []


# An engine run


async def engine_fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content="board notes", events=(cited(),))


def engine_world(governance: ApprovalPolicy, **overrides: object) -> EngineWorld:
    def guard(default: ApprovalPolicy, ledger: SourceLedger, run_id: str) -> ApprovalPolicy:
        assert isinstance(ledger, GrantingLedger)
        return engine_guarded(default, governance, rules=flow_rules(LOG), ledger=ledger, run_id=run_id, log=LOG)

    settings: dict[str, object] = {
        "tools": ENGINE_TOOLS,
        "handlers": {FETCH_RECORD_TOOL_NAME: engine_fetch},
        "ledger": lambda: GrantingLedger(SessionSourceLedger(tool_names={t.effect.tool_id: t.name for t in ENGINE_TOOLS})),
        "guard": guard,
        "extension_writers": {CITATIONS_EVENT: frozenset({"worker"})},
    }
    return EngineWorld(**{**settings, **overrides})  # type: ignore[arg-type]


async def engine_reasons(w: EngineWorld) -> list[Mapping[str, JsonValue]]:
    w.admit(RUN, 'tool create {"channel": "sales", "text": "hello"}\nsay waiting')
    assert (await w.attempt(RUN)).status == "suspended"
    assert len([e for e in w.events(RUN) if e.type == "interaction.requested"]) == 1
    return [e.data for e in w.events(RUN) if e.type == "interaction.explained"]


async def test_f6_1_an_engine_run_held_by_the_org_names_the_org() -> None:
    [reason] = await engine_reasons(engine_world(org_asks()))
    assert (reason["rule"], reason["layer"], reason["conversationGrant"]) == ("writes", "deployment", False)


async def test_f6_2_an_engine_runs_own_default_names_the_engine() -> None:
    [reason] = await engine_reasons(engine_world(LayeredApprovalPolicy()))
    assert (reason["rule"], reason["layer"]) == (ENGINE_WRITES_RULE, "engine")


async def test_f6_6_an_engine_run_on_an_older_node_writes_what_it_accepts(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    [default] = await engine_reasons(engine_world(LayeredApprovalPolicy(), journal_accepts=OLDER_NODE))
    assert default["rule"] == ENGINE_WRITES_RULE and "layer" not in default
    assert "tool.approval_reason_withheld" not in caplog.text
    assert await engine_reasons(engine_world(org_asks(), journal_accepts=OLDER_NODE)) == []
    assert "tool.approval_reason_withheld" in caplog.text
