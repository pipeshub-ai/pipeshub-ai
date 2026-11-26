"""R50JFIX-1 (MR-17): a loop run its budget cut before any answer must name the file it saved, on every surface.

The lane's budget journeys (agent.quick, the assistant, resume, cron, /mcp, Slack) saw a finalized answer of ""
while the settled outcome's English closing said "It saved **journey-….md**": the web chat never shows that closing
(it words the stop from the outcome) and /mcp answers with the saved answer alone. R36COVER-FU-5's test built the
``RunEnding`` by hand, so it never saw what the kernel and the presenter journal together. These cases run the real
``DurableKernel`` to the budget cut with no reserve for an answer-only turn, through ``PipesHubPresenter`` and the
loop assembler's own settle step (``cut_short_settle``), and read the journal: the answer every surface shows (the
web chat's text, /mcp's answer, the Slack bot's message before the closing) names the saved file and what the run
gathered by plain label; the closing carries only the stop and how to go on, so Slack does not say it twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.artifacts.events import presented_event
from app.harness.assembly.assembler import cut_short_settle
from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_WRITERS
from app.harness.labels import PipeshubReplay
from app.harness.presentation import PipesHubPresenter
from harness_core.artifacts.types import ArtifactCard
from harness_core.engines.outcome.deliverable import ANSWER
from harness_core.kernel.durable import ToolExecution
from harness_core.provenance.ledger import ProvenanceFeed, ProvenanceLedger
from harness_core.tools.registry import ToolRegistry
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted
from tests.unit.harness_core.durable.test_budgetend import FakeWrapUp, Ledgered

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import ToolContext

NAME = "journey-r50.md"
SAVE = "artifacts__save_artifact"


def _world() -> World:
    world = World(extension_writers=EVENT_WRITERS)  # type: ignore[arg-type]

    async def save(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        card = ArtifactCard("a-1", 1, str(args["name"]), "text/markdown", 9, "DOCUMENT", "final")
        return ToolExecution(status="ok", content={"success": True, "name": args["name"]}, events=(presented_event(card),))

    async def search(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"hits": [str(args["query"])]})

    world.tools.add(SAVE, save, tags=[("category", "write")])
    world.tools.add("knowledgegraph__search", search, tags=[("category", "read")])
    world.admit(text=f"Make a file {NAME} with the notes", max_turns=8)
    return world


async def _cut(world: World) -> dict[str, Any]:
    _, port = scripted(
        reply("", (SAVE, {"name": NAME, "content": "notes"})),
        reply("", ("knowledgegraph__search", {"query": "onboarding"})),
        reply("never written"),
    )
    fed = ProvenanceFeed(ProvenanceLedger(PipeshubReplay()))
    context = make_context(protocol="agui", run_id=RUN)
    presenter = PipesHubPresenter(context, ToolRegistry())
    presenter.use_outcome(list, settle=cut_short_settle(fed, RUN, ANSWER))
    wrap = FakeWrapUp(can_hold=False)
    done = await world.run(world.kernel(Ledgered(port, wrap, allow=2), wrap_up=wrap, presenter=presenter, ledger=fed))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert dict(world.events("run.completed")[0].data) == {"outcome": "answered", "truncatedBy": "budget"}
    [finalized] = world.events("x.pipeshub.answer.finalized")
    [settled] = world.events("x.pipeshub.outcome.settled")
    return {"answer": finalized.data["answer"], "parts": finalized.data["partTexts"], "settled": settled.data}


async def test_r50jfix_1_the_cut_answer_every_surface_shows_names_the_saved_file_and_what_it_gathered() -> None:
    cut = await _cut(_world())

    answer = cut["answer"]
    assert f"It saved **{NAME}** in this conversation's files." in answer
    assert "What it had gathered so far:\n- Save artifact\n- Search" in answer
    assert SAVE not in answer and "knowledgegraph__search" not in answer
    assert answer in cut["parts"]


async def test_r50jfix_1_the_closing_says_only_the_stop_and_how_to_go_on() -> None:
    cut = await _cut(_world())

    settled = cut["settled"]
    closing = settled["presented"]["closing"]
    assert closing.startswith("---") and "spending limit" in closing and 'Send "continue"' in closing
    assert NAME not in closing and "Save artifact" not in closing
    assert settled["outcome"]["kind"] == "partial" and settled["outcome"]["reason"] == "budget"
    assert settled["outcome"]["action"] == "continue"
