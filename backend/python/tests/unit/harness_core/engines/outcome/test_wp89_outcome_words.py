"""WP89-WORDS: the settled outcome is the source of the words a person reads about it (ADR-RUN-01 D1, WP-89).

The presenter frames an answer in two parts, the model's prose and its own English closing. A journal whose chat words
the outcome itself (``outcome_words``) is sent only the prose, and the settled outcome carries the closing for surfaces
with no UI; a journal that does not take it (an older Node in a rolling upgrade) keeps the whole English answer. A
parent's model always reads the whole text."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.contracts.generated.common import ContentRef
from harness_core.effects.types import ToolExecution
from harness_core.engines.outcome.ledger import (
    OUTCOME_LEDGER,
    OUTCOME_SETTLED,
    ArtifactWritten,
    SourceGathered,
)
from harness_core.engines.outcome.outcome import Completed, Delivered, Partial
from harness_core.engines.outcome.presenter import ANSWER_SUMMARY_CHARS, PRESENTER
from harness_core.journal.types import OUTCOME_WORDS
from harness_core.testing.engines.durable import (
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall

RUN = "run-wp89-words"
REQUEST = "Research how Onyx compares with PipesHub. Write the final report as one Markdown file named report.md in out/"
PAGES = tuple(SourceGathered(f"https://docs.onyx.app/{n}", "fetch_url", f"https://docs.onyx.app/{n}", f"P{n}") for n in range(3))
REPORT = "# Onyx vs PipesHub\n\n## Executive summary\n\nOnyx documents its connectors.\n\n## Finding\n\n" + "detail " * 2000
ENGINE_FILE = ArtifactWritten("out/report.md", 7, len(REPORT), "final", "engine")
WRAPPED_FILE = ArtifactWritten("out/report.md", 8, 900, "final", "wrap_up")

CASES = [
    pytest.param(Completed(Delivered()), " done ", id="completed"),
    pytest.param(Partial("turns", Delivered(ENGINE_FILE, PAGES, kept=True)), REPORT, id="engine-file-opening"),
    pytest.param(Partial("budget", Delivered(WRAPPED_FILE)), "x", id="wrap-up-file"),
    pytest.param(Partial("unfinished", Delivered(WRAPPED_FILE, PAGES)), "Report", id="written-from-gathered"),
    pytest.param(Partial("time", Delivered(sources=PAGES, kept=True), ("report.md",)), "", id="unwritten-kept"),
    pytest.param(Partial("time", Delivered(), ("report.md",)), "", id="unwritten-nothing"),
    pytest.param(Partial("stall", Delivered(sources=PAGES)), "Half an answer.", id="prose-cut"),
    pytest.param(Partial("budget", Delivered()), "", id="no-answer"),
]


@pytest.mark.parametrize(("outcome", "body"), CASES)
def test_wp89_words_1_the_framing_joins_to_the_english_answer_and_the_prose_is_only_the_models(
    outcome: Completed | Partial, body: str
) -> None:
    framed = PRESENTER.frame(outcome, body=body)
    assert framed.text == PRESENTER.answer(outcome, body=body) == PRESENTER.present(outcome, body=body).answer
    for word in ("**This result is partial.**", "isn't written yet", "The full report is in", "This reply stopped"):
        assert word not in framed.prose, "the presenter's words are never the prose"
    assert framed.prose in body.strip()


def test_wp89_words_2_a_long_report_shows_its_opening_and_says_so_only_in_the_closing() -> None:
    framed = PRESENTER.frame(Partial("turns", Delivered(ENGINE_FILE, PAGES, kept=True)), body=REPORT)
    assert framed.opening and framed.prose.startswith("# Onyx vs PipesHub") and len(framed.prose) < ANSWER_SUMMARY_CHARS
    assert framed.closing.startswith("The full report is in **report.md**.")
    short = PRESENTER.frame(Partial("turns", Delivered(ENGINE_FILE)), body="A short report.")
    assert not short.opening and short.prose == "A short report."


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    url = str(call.arguments.get("url"))
    return ToolExecution(status="ok", content=json.dumps({"ok": True, "url": url, "title": "Onyx", "blocks": []}))


def _lookups(count: int) -> list[str]:
    return [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/p{n}"})}' for n in range(count)]


async def _cut_run(accepts: frozenset[str] | None) -> tuple[EngineWorld, str]:
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={"lookup": _fetch}, wrap_up=None, journal_accepts=accepts)
    world.admit(RUN, "\n".join([REQUEST, *_lookups(12), "say done"]), spec=engine_run_spec(engine_profile(max_steps=6)))
    assert (await world.attempt(RUN)).status == "completed"
    (final,) = [e for e in world.events(RUN) if e.type == "message.completed" and e.data.get("isFinal")]
    text = (await world.content.get(ContentRef.model_validate(final.data["textRef"]))).decode("utf-8")
    return world, text


def _settled(world: EngineWorld) -> dict[str, Any]:
    (event,) = [e for e in world.events(RUN) if e.type == OUTCOME_SETTLED]
    return event.data


async def test_wp89_words_3_a_chat_that_words_the_outcome_gets_only_the_prose_and_the_outcome_carries_the_closing() -> None:
    world, answer = await _cut_run(frozenset({OUTCOME_LEDGER, OUTCOME_WORDS}))
    assert answer == "", "the run owes report.md and wrote no prose: the chat words the whole outcome"
    settled = _settled(world)
    assert settled["outcome"]["kind"] == "partial" and settled["outcome"]["action"] == "continue"
    closing = settled["presented"]["closing"]
    assert closing.startswith("**report.md isn't written yet.**") and settled["presented"]["opening"] is False
    assert 'Send "continue" to write report.md from it.' in closing


async def test_wp89_words_4_an_older_node_keeps_the_whole_english_answer_and_no_framing() -> None:
    world, answer = await _cut_run(frozenset({OUTCOME_LEDGER}))
    assert answer.startswith("**report.md isn't written yet.**"), "a Node that does not word outcomes keeps the words"
    assert "presented" not in _settled(world)


async def test_wp89_words_5_a_child_result_carries_its_framing_and_the_parent_relays_it() -> None:
    """A code_task child's result carries its settled outcome with the closing; a parent that presents the child's
    answer as its own shows the prose and journals the child's outcome again, so its chat words it (WP89-RELAY)."""
    from app.harness.assembly.deep_route import HANDOFF_SLOT, Handoff
    from harness_core.engines.outcome.briefing import ChildReport
    from harness_core.kernel.durable.ports import TurnResult
    from tests.unit.harness.assembly.test_deeproute import _director, _state

    framed = PRESENTER.frame(Partial("time", Delivered(ENGINE_FILE, PAGES, kept=True)), body="The findings so far.")
    outcome = {
        "kind": "partial", "reason": "time", "missing": [], "action": "continue", "answered": True,
        "delivered": {"file": {"path": "out/report.md", "revision": 7, "by": "engine"}, "sources": 3, "notes": [], "kept": True},
        "presented": {"closing": framed.closing, "opening": False}, "deliverable": {"kind": "file", "name": "report.md"},
    }
    content = {"success": True, "output": framed.text, "truncatedBy": "time", "outcome": outcome}
    report = ChildReport.read(content)
    assert report.prose == "The findings so far." and report.closing == framed.closing
    asked = Handoff(turn=1, trigger="upfront", reason="analysis")
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: asked.slot()})
    result = [TurnResult(name="code_task", call_id="c1.0", status="ok", content=content)]
    worded = _director(worded=True).before(2, state, result)
    assert worded.synthetic is not None and worded.synthetic.content[0].text == "The findings so far."  # type: ignore[union-attr]
    (relayed,) = worded.events
    assert relayed.type == OUTCOME_SETTLED and relayed.data["presented"] == {"closing": framed.closing, "opening": False}
    assert relayed.data["deliverable"] == {"kind": "file", "name": "report.md"} and "answered" not in relayed.data["outcome"]  # type: ignore[operator]
    plain = _director().before(2, state, result)
    assert plain.synthetic is not None and plain.synthetic.content[0].text == framed.text and plain.events == ()  # type: ignore[union-attr]


def test_wp89_words_6_a_failed_runs_words_are_the_presenters_from_the_typed_outcome() -> None:
    """WP-84's ``mcp_tool_withdrawn`` sentence is the presenter's (``outcome.failed.*``), from a typed ``Failed``
    carrying the servers' labels; nothing outside the outcome package words it."""
    import harness_core.kernel.durable.capability as capability
    from harness_core.engines.outcome.outcome import Failed

    assert not hasattr(capability, "withdrawn_message")
    text = PRESENTER.failure(Failed("mcp_tool_withdrawn", Delivered(), servers=("Atlassian", "GitHub", "Jira")))
    assert text.startswith("Atlassian, GitHub and Jira no longer offers a tool this task was using")
