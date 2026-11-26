"""SEC1 FL-5..FL-8, FL-10, FL-11: the lethal trifecta, tried by a scripted engine through the run's real tool
endpoint, effect runner and journal, with PipesHub's session ledger and flow settings.

The engine fetches a restricted record (the record tool journals what it read, as PipesHub's citation
sink does) and then calls an external write tool with the record's text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rule, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects import ToolExecution
from harness_core.effects.flow import FLOW_RULE, FlowGuardedPolicy, FlowRule
from harness_core.effects.policy import LayeredApprovalPolicy
from harness_core.journal.types import NewEvent
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.durable import CREATE, EngineWorld

if TYPE_CHECKING:
    from harness_core.effects.types import ApprovalPolicy, ToolContext
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.security.labels import SourceLedger

RUN = "run-flow"
FETCH = offered(FETCH_RECORD_TOOL_NAME)
LOOKUP = offered("lookup")
SECRET = "Q3 acquisition target: Contoso"
LOG = HarnessLog.named("tests.sec1")
READ_THEN_WRITE = f'tool {FETCH_RECORD_TOOL_NAME} {{"recordId": "rec-1"}}\ntool create {{"text": "{SECRET}"}}\nsay done'


async def fetch_record(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "DRIVE", "recordName": "Board notes"}}}
    return ToolExecution(status="ok", content=SECRET, events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


def guard(policy: ApprovalPolicy, ledger: SourceLedger, run_id: str) -> ApprovalPolicy:
    return flow_guarded(policy, rules=flow_rules(LOG), ledger=ledger, run_id=run_id, log=LOG)


def world(**overrides: object) -> EngineWorld:
    tools = (FETCH, LOOKUP, CREATE)
    settings: dict[str, object] = {
        "tools": tools,
        "handlers": {FETCH_RECORD_TOOL_NAME: fetch_record},
        # Without the flow rule nothing asks: the base policy allows every tool of this run.
        "approvals": {"create": "allow"},
        "ledger": lambda: SessionSourceLedger(tool_names={t.effect.tool_id: t.name for t in tools}),
        "guard": guard,
        "extension_writers": {CITATIONS_EVENT: frozenset({"worker"})},
    }
    return EngineWorld(**{**settings, **overrides})  # type: ignore[arg-type]


def creates(w: EngineWorld) -> int:
    return sum(count for (name, _), count in w.effects.items() if name == "create")


def denied_rules(w: EngineWorld) -> list[object]:
    return [e.data.get("rule") for e in w.events(RUN) if e.type == "tool.denied"]


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def turn_on(level: str = "deny", above: str | None = None) -> None:
        monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
        monkeypatch.setenv(FLOW_LEVEL_ENV, level)
        if above is not None:
            monkeypatch.setenv(FLOW_ABOVE_ENV, above)

    monkeypatch.setenv(HARNESS_FLOW_GUARD, "false")
    monkeypatch.delenv(FLOW_LEVEL_ENV, raising=False)
    monkeypatch.delenv(FLOW_ABOVE_ENV, raising=False)
    return turn_on


async def test_fl6_deny_a_write_after_a_restricted_read_is_refused_and_never_runs(flow) -> None:
    flow("deny")
    w = world()
    w.admit(RUN, READ_THEN_WRITE)
    assert (await w.attempt(RUN)).status == "completed"
    assert creates(w) == 0
    assert denied_rules(w) == [FLOW_RULE]
    assert SECRET not in "".join(str(e.data) for e in w.events(RUN) if e.type == "tool.intent")


async def test_fl6_ask_a_write_after_a_restricted_read_waits_for_a_person(flow) -> None:
    flow("ask")
    w = world()
    w.admit(RUN, READ_THEN_WRITE.replace("say done", "say waiting"))
    disposition = await w.attempt(RUN)
    assert disposition.status == "suspended"
    assert disposition.reason is not None and disposition.reason.startswith("ix.approve.k")
    assert "interaction.requested" in w.types(RUN)
    assert creates(w) == 0


async def test_fl6_off_the_base_policy_decides_as_today(flow) -> None:
    w = world()
    w.admit(RUN, READ_THEN_WRITE)
    assert (await w.attempt(RUN)).status == "completed"
    assert creates(w) == 1
    assert denied_rules(w) == []


async def test_fl7_a_write_without_a_read_is_not_narrowed(flow) -> None:
    flow("deny")
    w = world()
    w.admit(RUN, 'tool create {"text": "hello"}\nsay done')
    assert (await w.attempt(RUN)).status == "completed"
    assert creates(w) == 1


async def test_fl8_the_read_survives_a_resume_in_a_new_attempt(flow) -> None:
    flow("ask")
    w = world()
    w.admit(RUN, READ_THEN_WRITE.replace("say done", "say waiting"))
    first = await w.attempt(RUN)
    assert first.status == "suspended" and first.reason is not None
    _ = w.journal.queue_input(RUN, 'tool create {"text": "another"}\nsay waiting')
    w.journal.resolve_interaction(RUN, first.reason, "denied")
    second = await w.attempt(RUN)
    assert second.status == "suspended" and second.reason not in (None, first.reason)
    assert creates(w) == 0


async def test_fl10_an_opaque_read_makes_the_context_unknown(flow) -> None:
    flow("deny", above="private")
    w = world()
    w.admit(RUN, 'tool lookup {"q": "x"}\ntool create {"text": "x"}\nsay done')
    assert (await w.attempt(RUN)).status == "completed"
    assert creates(w) == 0
    assert denied_rules(w) == [FLOW_RULE]


async def test_fl10_at_the_private_threshold_a_named_record_alone_passes(flow) -> None:
    flow("deny", above="private")
    w = world()
    w.admit(RUN, READ_THEN_WRITE)
    assert (await w.attempt(RUN)).status == "completed"
    assert creates(w) == 1


async def test_fl11_an_earlier_run_of_the_session_taints_this_one(flow) -> None:
    flow("deny")
    w = world()
    w.admit("run-earlier", f'tool {FETCH_RECORD_TOOL_NAME} {{"recordId": "rec-1"}}\nsay read')
    assert (await w.attempt("run-earlier")).status == "completed"
    w.admit(RUN, 'tool create {"text": "x"}\nsay done')
    assert (await w.attempt(RUN)).status == "completed"
    assert creates(w) == 0
    assert denied_rules(w) == [FLOW_RULE]


def test_fl5_settings(flow, caplog: pytest.LogCaptureFixture) -> None:
    assert flow_rule(LOG) is None
    base = LayeredApprovalPolicy()
    assert flow_guarded(base, rules=flow_rules(LOG), ledger=SessionSourceLedger(), run_id=RUN, log=LOG) is base
    flow("deny", above="private")
    assert flow_rule(LOG) == FlowRule("deny", "private")
    guarded = flow_guarded(base, rules=flow_rules(LOG), ledger=SessionSourceLedger(), run_id=RUN, log=LOG)
    assert isinstance(guarded, FlowGuardedPolicy) and guarded.base is base
    assert flow_guarded(base, rules=flow_rules(LOG), ledger=None, run_id=RUN, log=LOG) is base
    flow("sometimes", above="nowhere")
    assert flow_rule(LOG) == FlowRule("ask", "public")
    assert caplog.text.count("approvals.flow_setting_invalid") == 2
