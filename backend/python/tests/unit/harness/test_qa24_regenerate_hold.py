"""QA24-5 (APR-G4, APR-23): regenerate of a turn that read private data holds its write on the durable path (R1 F2).

Regenerate admits a new run of the session whose imported history replaces the conversation without the turn it
regenerates (UX3). The replaced run's read is gone from the model's view but not from what the session has seen: the
new run's model can post straight away, from what it remembers nowhere, and the flow rule must still hold that post
for a person. The kernel feeds PipesHub's session ledger as in production (SEC1 FL-9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.labels import (
    HISTORY_LABELLED_EVENT,
    SessionSourceLedger,
    label_wire,
    own_work_label,
)
from app.harness.unpinned import FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.journal import LegacyImport
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from harness_core.security.labels import join_labels
from tests.unit.harness_core.durable.support import RUN, SESSION, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.core.messages import AssistantMessage
    from harness_core.kernel.durable import ToolContext
    from harness_core.kernel.durable.ports import RunEnding
    from harness_core.testing import Reply

REGENERATED = "run-regenerated"
QUESTION = "what is the acquisition target?"
SECRET = "Q3 acquisition target: Contoso"
READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))
POST = reply("", ("post_message", {"text": SECRET}))


ANSWER = "x.pipeshub.answer.finalized"


@dataclass(frozen=True)
class LabelPresenter:
    """Records a finished run's label the way ``PipesHubPresenter`` does (``own_work_label`` of its events), without
    the legacy answer finalizer: what a later run of the session reads that run by."""

    run_id: str

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        return []

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        return []

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        label = label_wire(own_work_label(ending.events, self.run_id))
        return [NewEvent(ANSWER, {"outcome": ending.outcome, "label": label}, critical=False)]


async def fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    cited: dict[str, JsonValue] = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE"}}}
    return ToolExecution(status="ok", content=SECRET, events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    monkeypatch.delenv(FLOW_LEVEL_ENV, raising=False)


def _world() -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"}), ANSWER: frozenset({"worker"})})
    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    world.admit(text=QUESTION)
    return world


async def _run(world: World, run_id: str, *replies: Reply) -> str:
    _, port = scripted(*replies)
    ledger = SessionSourceLedger()
    log = HarnessLog.named("tests.qa24")
    approval = flow_guarded(allow_all, rules=flow_rules(log), ledger=ledger, run_id=run_id, log=log)
    return (await world.run(world.kernel(port, approval=approval, ledger=ledger, presenter=LabelPresenter(run_id)), run_id)).status


def _regenerate(world: World) -> None:
    """Node's regenerate admission: the conversation without the replaced turn (nothing before it here), journaled
    with the label Node derives for what it imports (``historyLabelledEvent``), which nothing private is in."""
    imported = LegacyImport([], 0, replaces_history=True)
    world.admit(REGENERATED, imported=imported, text=QUESTION)
    _ = world.journal.inject(
        SESSION,
        {
            "runId": REGENERATED,
            "type": HISTORY_LABELLED_EVENT,
            "writer": "node",
            "critical": False,
            "data": {"transcriptDigest": imported.data()["transcriptDigest"], "label": label_wire(join_labels([]))},
        },
    )


def _posts(world: World) -> int:
    return sum(1 for e in world.saas.effects if e.op == "post_message")


async def test_qa24_5_regenerating_an_answered_turn_that_read_private_data_holds_the_post() -> None:
    world = _world()
    assert await _run(world, RUN, READ, reply("It is Contoso.")) == "completed"
    _regenerate(world)
    assert await _run(world, REGENERATED, POST, reply("posted")) == "suspended"
    assert _posts(world) == 0
    assert "interaction.requested" in world.types(REGENERATED)


async def test_qa24_5_regenerating_while_the_post_waits_for_a_person_holds_it_again() -> None:
    world = _world()
    assert await _run(world, RUN, READ, POST, reply("posted")) == "suspended"
    world.journal.request_cancel(RUN)
    _regenerate(world)
    assert await _run(world, REGENERATED, POST, reply("posted")) == "suspended"
    assert _posts(world) == 0
    assert "interaction.requested" in world.types(REGENERATED)


async def test_qa24_5_regenerating_a_turn_that_read_nothing_posts() -> None:
    world = _world()
    assert await _run(world, RUN, reply("I do not know.")) == "completed"
    _regenerate(world)
    assert await _run(world, REGENERATED, POST, reply("posted")) == "completed"
    assert _posts(world) == 1
