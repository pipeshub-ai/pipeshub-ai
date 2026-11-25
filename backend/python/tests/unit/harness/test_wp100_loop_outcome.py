"""WP100-LOOPOUT-1..6 (ADR-RUN-01 D1): a loop run that a limit cut short journals its settled outcome, so the chat
words it in the reader's language like every other run's.

Before, only an engine run (and a hand-off that presented its child's answer) journaled ``x.pipeshub.outcome.settled``:
the loop's own no-model answer (``cut_short_fallback``) was saved as the presenter's English and the chat added its
cut-short note beside it. Now, while the journal takes the framing (``outcome_words``), the loop's cut-short answer
settles as ``Partial(reason)``: the no-model answer's prose is empty (the chat words it all) and its English rides as
the closing for surfaces with no UI; an answer the model wrote keeps its words with the presenter's closing. A
hand-off that already relayed its child's outcome is not settled twice, and a completed run is unchanged.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.harness.presentation import PipesHubPresenter
from harness_core.engines.outcome.deliverable import ANSWER
from harness_core.engines.outcome.ledger import (
    OUTCOME_SETTLED,
    OutcomeLedger,
    SourceGathered,
)
from harness_core.engines.outcome.loop import loop_settled
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.turn_limit import cut_short_fallback
from harness_core.kernel.durable.ports import RunEnding
from harness_core.tools.registry import ToolRegistry
from tests.unit.agents.adapter.conftest import make_context

RUN = "run-1"
FINALIZED = "x.pipeshub.answer.finalized"
CALLS = (ToolCall(id="c1", name="web_search", arguments={}), ToolCall(id="c2", name="web_search", arguments={}))
LEDGER = OutcomeLedger()
_ = LEDGER.record(SourceGathered("https://a.example/", "web_page", "https://a.example/"))


def _relayed() -> JournalEvent:
    return JournalEvent(
        org_id="o", session_id="s", stream_id="s", seq=9, run_id=RUN, type=OUTCOME_SETTLED, schema_version=1,
        critical=False, ts="2026-11-06T00:00:00.000Z", writer="worker", visibility="owner",
        data={"outcome": {"kind": "partial"}},
    )


def _presenter(*, worded: bool = True) -> PipesHubPresenter:
    presenter = PipesHubPresenter(make_context(conversation_id="s-1", protocol="agui", run_id=RUN), ToolRegistry())
    presenter.use_outcome(
        list, settle=(lambda ending: loop_settled(ending, RUN, LEDGER, ANSWER)) if worded else None
    )
    return presenter


def _by_type(events: list[Any], kind: str) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", e.data) for e in events if e.type == kind]


def test_wp100_loopout_1_the_no_model_answer_settles_partial_with_what_it_did_as_its_prose() -> None:
    answer = cut_short_fallback("turns", ["web_search", "web_search"])
    settled = loop_settled(
        RunEnding(outcome="answered", answer=answer, truncated_by="turns", tool_calls=CALLS), RUN, LEDGER, ANSWER
    )

    assert settled is not None
    # R36COVER-FU-5, R50JFIX-1: what it read is the prose every surface shows; the stop and the action are the closing
    assert settled.framed.prose == PRESENTER.gathered(["web_search", "web_search"], sources=LEDGER.sources)
    worded = settled.framed.closing
    assert worded.startswith("---") and "continue" in worded and "Web search" not in worded
    data = cast("dict[str, Any]", settled.event.data)
    assert data["outcome"]["kind"] == "partial" and data["outcome"]["reason"] == "turns"
    assert data["outcome"]["delivered"]["sources"] == 1
    # The conversation keeps every result it gathered, so "continue" carries on from them.
    assert data["outcome"]["action"] == "continue" and data["outcome"]["delivered"]["kept"] is True
    assert data["presented"] == {"closing": worded, "opening": False}


def test_wp100_loopout_2_an_answer_the_model_wrote_keeps_its_words_with_the_presenters_closing() -> None:
    settled = loop_settled(
        RunEnding(outcome="answered", answer="Here is what I found so far.", truncated_by="budget"), RUN, LEDGER, ANSWER
    )

    assert settled is not None
    assert settled.framed.prose == "Here is what I found so far."
    assert settled.framed.closing.startswith("---") and settled.framed.closing
    # Nothing was gathered by a tool and no file kept: trying again is the action.
    assert cast("dict[str, Any]", settled.event.data)["outcome"]["action"] == "retry"


def test_wp100_loopout_3_a_completed_run_a_failed_one_or_a_relayed_hand_off_is_not_settled_here() -> None:
    assert loop_settled(RunEnding(outcome="answered", answer="Done."), RUN, LEDGER, ANSWER) is None
    assert loop_settled(RunEnding(outcome="error", error_type="x", truncated_by="turns"), RUN, LEDGER, ANSWER) is None
    relayed = RunEnding(outcome="answered", answer="The engine's partial.", truncated_by="time", events=(_relayed(),))
    assert loop_settled(relayed, RUN, LEDGER, ANSWER) is None


async def test_wp100_loopout_4_the_presenter_journals_the_outcome_and_saves_only_the_prose() -> None:
    answer = cut_short_fallback("time", ["web_search"])
    events = await _presenter().finalize(
        RunEnding(outcome="answered", texts=("Let me look.",), answer=answer, truncated_by="time", tool_calls=CALLS[:1])
    )

    [finalized] = _by_type(events, FINALIZED)
    assert finalized["answer"] == PRESENTER.gathered(["web_search"], sources=LEDGER.sources)
    assert answer.strip() not in finalized["partTexts"]
    assert finalized["truncatedBy"] == "time"
    [settled] = _by_type(events, OUTCOME_SETTLED)
    assert settled["presented"]["closing"].startswith("---")
    assert "Web search" not in settled["presented"]["closing"]
    # The settled outcome follows the answer, as the engine's does.
    assert [e.type for e in events].index(FINALIZED) < [e.type for e in events].index(OUTCOME_SETTLED)


async def test_wp100_loopout_5_a_model_written_cut_short_answer_is_saved_as_written() -> None:
    events = await _presenter().finalize(
        RunEnding(outcome="answered", texts=("Partial findings.",), answer="Partial findings.", truncated_by="turns")
    )

    [finalized] = _by_type(events, FINALIZED)
    assert finalized["answer"] == "Partial findings."
    assert len(_by_type(events, OUTCOME_SETTLED)) == 1


async def test_wp100_loopout_6_a_journal_that_does_not_take_the_framing_keeps_the_english_answer() -> None:
    answer = cut_short_fallback("turns", ["web_search"])
    events = await _presenter(worded=False).finalize(
        RunEnding(outcome="answered", answer=answer, truncated_by="turns", tool_calls=CALLS[:1])
    )

    [finalized] = _by_type(events, FINALIZED)
    assert finalized["answer"] == answer.strip() or finalized["answer"] == answer
    assert _by_type(events, OUTCOME_SETTLED) == []


@pytest.mark.parametrize("worded", [True, False], ids=["outcome_words", "older_node"])
async def test_wp100_loopout_7_the_assembled_loop_run_settles_its_cut_short_answer_when_the_chat_words_it(
    worded: bool,
) -> None:
    """The wiring: the loop assembler gives its presenter the settle step only for a journal that takes the
    framing (``outcome_words``); an older Node's run keeps its English answer and journals no settled outcome."""
    import dataclasses

    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from harness_core.journal.types import OUTCOME_LEDGER, OUTCOME_WORDS
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort
    from tests.unit.harness.assembly import world as w

    accepts = frozenset({OUTCOME_LEDGER, OUTCOME_WORDS} if worded else {OUTCOME_LEDGER})
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    with w.deployment():
        assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
        claim = dataclasses.replace(w.claim(w.pinned_spec(tool_ids=[]), query="find the bug"), accepts=accepts)
        runtime = await assembler.build(claim)  # type: ignore[arg-type]
        try:
            answer = cut_short_fallback("turns", ["web_search"])
            events = await runtime.presenter.finalize(  # type: ignore[union-attr]
                RunEnding(outcome="answered", answer=answer, truncated_by="turns", tool_calls=CALLS[:1])
            )
        finally:
            await runtime.close()

    [finalized] = _by_type(events, FINALIZED)
    settled = _by_type(events, OUTCOME_SETTLED)
    if worded:
        assert finalized["answer"] == "What it had gathered so far:\n- Web search"
        assert [s["outcome"]["reason"] for s in settled] == ["turns"]
    else:
        assert finalized["answer"].strip() == answer.strip()
        assert settled == []
