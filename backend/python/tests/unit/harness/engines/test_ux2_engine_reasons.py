"""UX2 EA-1..EA-6, VS-4 (engines): an engine run's held write is explained like the durable loop's, and a person may
allow its tool and destination for the rest of the conversation unless an org rule holds it.

A scripted engine through the run's real tool endpoint, effect runner and journal, with PipesHub's session ledger,
destinations and ``engine_guarded`` composition (the assembler's).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import engine_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects import ToolExecution
from harness_core.effects.flow import ENGINE_WRITES_RULE, FLOW_RULE, GrantingLedger
from harness_core.effects.policy import WRITES_ASK, ApprovalRules, LayeredApprovalPolicy
from harness_core.journal.types import NewEvent
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.durable import CREATE, EngineWorld

if TYPE_CHECKING:
    from harness_core.effects.types import ApprovalPolicy, ToolContext
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.security.labels import SourceLedger

RUN = "run-ea"
FETCH = offered(FETCH_RECORD_TOOL_NAME)
LOG = HarnessLog.named("tests.ux2")
TOOLS = (FETCH, CREATE)


async def fetch_record(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Board notes"}}}
    return ToolExecution(status="ok", content="board notes", events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


def world(org: ApprovalPolicy | None = None, **overrides: object) -> EngineWorld:
    governance = org if org is not None else LayeredApprovalPolicy()

    def guard(default: ApprovalPolicy, ledger: SourceLedger, run_id: str) -> ApprovalPolicy:
        assert isinstance(ledger, GrantingLedger)
        return engine_guarded(default, governance, rules=flow_rules(LOG), ledger=ledger, run_id=run_id, log=LOG)

    settings: dict[str, object] = {
        "tools": TOOLS,
        "handlers": {FETCH_RECORD_TOOL_NAME: fetch_record},
        "ledger": lambda: GrantingLedger(SessionSourceLedger(tool_names={t.effect.tool_id: t.name for t in TOOLS})),
        "guard": guard,
        "extension_writers": {CITATIONS_EVENT: frozenset({"worker"})},
    }
    return EngineWorld(**{**settings, **overrides})  # type: ignore[arg-type]


def create(channel: str | None, text: str) -> str:
    args = f'"channel": "{channel}", ' if channel is not None else ""
    return f'tool create {{{args}"text": "{text}"}}'


READ = f'tool {FETCH_RECORD_TOOL_NAME} {{"recordId": "rec-1"}}'


def explained(w: EngineWorld, run: str = RUN) -> list[JournalEvent]:
    return [e for e in w.events(run) if e.type == "interaction.explained"]


def requested(w: EngineWorld, run: str = RUN) -> list[JournalEvent]:
    return [e for e in w.events(run) if e.type == "interaction.requested"]


def creates(w: EngineWorld) -> int:
    return sum(count for (name, _), count in w.effects.items() if name == "create")


# EA-1


async def test_ea1_a_held_write_says_why_engine_runs_ask_in_the_same_commit() -> None:
    w = world()
    w.admit(RUN, f"{create('sales', 'hello')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    [reason] = explained(w)
    [request] = requested(w)
    assert reason.critical is False and reason.writer == "worker"
    assert reason.data["interactionId"] == request.data["interactionId"]
    assert reason.data["rule"] == ENGINE_WRITES_RULE
    assert reason.data["sensitivity"] == "public"
    assert reason.data["sources"] == []
    assert reason.data["destination"]["label"] == "#sales"  # type: ignore[index]
    assert reason.data["conversationGrant"] is True
    types = [e.type for e in w.events(RUN)]
    # The approval line, then the reason, then the request.
    line = max(i for i, e in enumerate(w.events(RUN)) if e.type.endswith("engine.progress") and e.data.get("kind") == "approval")
    assert line < types.index("interaction.explained") < types.index("interaction.requested")
    assert reason.seq + 1 == request.seq


# EA-2


async def test_ea2_after_a_read_the_flow_reason_names_the_record_and_still_offers_the_grant() -> None:
    w = world()
    w.admit(RUN, f"{READ}\n{create('sales', 'notes')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    [reason] = explained(w)
    assert reason.data["rule"] == FLOW_RULE
    assert reason.data["sensitivity"] == "private"
    assert reason.data["sources"] == [{"title": "Board notes", "recordId": "rec-1"}]
    assert reason.data["conversationGrant"] is True


# EA-3


async def test_ea3_an_org_that_asks_for_every_write_is_never_granted() -> None:
    w = world(org=LayeredApprovalPolicy((WRITES_ASK,), ("org",)))
    w.admit(RUN, f"{create('sales', 'hello')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    [reason] = explained(w)
    # QA6-F6: the org's rule is the one named; what the run read and where the call goes are still said.
    assert (reason.data["rule"], reason.data["layer"]) == ("writes", "org")
    assert reason.data["sensitivity"] == "public"
    assert reason.data["destination"]["label"] == "#sales"  # type: ignore[index]
    assert reason.data["conversationGrant"] is False


# EA-4


async def test_ea4_no_destination_no_grant() -> None:
    w = world()
    w.admit(RUN, f"{create(None, 'hello')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    [reason] = explained(w)
    assert "destination" not in reason.data
    assert reason.data["conversationGrant"] is False


# EA-5, EA-6


async def _granted_first(w: EngineWorld, then: str) -> None:
    w.admit(RUN, f"{create('sales', 'one')}\nsay waiting")
    first = await w.attempt(RUN)
    assert first.status == "suspended" and first.reason is not None
    _ = w.journal.queue_input(RUN, then)
    w.journal.resolve_interaction(RUN, first.reason, "approved", scope="conversation")


async def test_ea5_ea6_the_grant_holds_for_the_tool_and_destination_on_a_new_attempt() -> None:
    w = world()
    await _granted_first(w, f"{create('sales', 'two')}\nsay done")
    assert (await w.attempt(RUN)).status == "completed"
    assert len(requested(w)) == 1
    assert creates(w) >= 1


async def test_ea5_a_new_destination_still_asks() -> None:
    w = world()
    await _granted_first(w, f"{create('general', 'two')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    assert len(requested(w)) == 2
    assert explained(w)[-1].data["destination"]["label"] == "#general"  # type: ignore[index]


async def test_ea5_a_later_run_of_the_conversation_keeps_the_grant() -> None:
    w = world()
    await _granted_first(w, "say done")
    assert (await w.attempt(RUN)).status == "completed"
    w.admit("run-later", f"{create('sales', 'three')}\nsay done")
    assert (await w.attempt("run-later")).status == "completed"
    assert requested(w, "run-later") == []


async def test_ea5_a_profile_deny_is_never_answered_by_a_grant() -> None:
    w = world(approvals={"create": "deny"})
    w.admit(RUN, f"{create('sales', 'one')}\nsay done")
    assert (await w.attempt(RUN)).status == "completed"
    assert creates(w) == 0
    assert [e.data.get("rule") for e in w.events(RUN) if e.type == "tool.denied"] == ["policy"]


async def test_ea5_a_grant_given_while_the_engine_waits_answers_its_next_call_in_the_same_turn() -> None:
    w = world()
    w.admit(RUN, f"{create('sales', 'one')}\n{create('sales', 'two')}\nsay done")

    async def decide() -> None:
        for _ in range(10_000):
            if requested(w):
                break
            await asyncio.sleep(0.001)
        ix = str(requested(w)[0].data["interactionId"])
        w.journal.resolve_interaction(RUN, ix, "approved", scope="conversation")
        w.wakeup.wake(RUN)

    person = asyncio.ensure_future(decide())
    done = await w.attempt(RUN, kernel=w.kernel(approval_hold_s=5.0))
    await person
    assert done.status == "completed"
    assert len(requested(w)) == 1
    assert creates(w) == 2


# VS-4 on an engine run


async def test_vs4_an_older_journal_gets_the_engine_ask_without_a_reason() -> None:
    w = world(journal_accepts=frozenset(), journal_refuses=frozenset({"interaction.explained"}))
    w.admit(RUN, f"{create('sales', 'hello')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    assert explained(w) == []
    assert len(requested(w)) == 1


def test_ea_rules_compose_as_documented() -> None:
    """The org's tool rule asks for a read: no engine default applies, so there is nothing of the engine's to say."""
    assert ApprovalRules(tools={"lookup": "ask"}).decide(FETCH.effect) == "allow"
