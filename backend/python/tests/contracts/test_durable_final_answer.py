"""PA-14: ``final_answer`` on the durable path, as legacy shows and ends it.

Frame parity for the two scenarios is D-5 (``durable-frames.test.ts``); these
cases pin what frame normalization cannot see: the answer reaches the live bus
while the model is still answering (FA-1), and a batch that writes before it
answers completes with the journaled answer on whichever attempt runs the
write (FA-4), with the confidence the tool reported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.contracts import golden_support
from tests.contracts.test_durable_goldens import (
    AGENT_SCENARIOS,
    _final_snapshot,
    run_durably,
)
from tests.contracts.test_durable_journals import record

if TYPE_CHECKING:
    from harness_core.journal import JournalEvent
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

FINAL_ANSWER = [s for s in AGENT_SCENARIOS if s.env.get("PIPESHUB_ENABLE_FINAL_ANSWER") == "true"]
WITH_WRITE = next(s for s in FINAL_ANSWER if s.name == "agent_final_answer_with_write")


def _finalized(events: list[JournalEvent]) -> dict[str, object]:
    [finalized] = [e.data for e in events if e.type == "x.pipeshub.answer.finalized"]
    return dict(finalized)


@pytest.mark.parametrize("scenario", FINAL_ANSWER, ids=[s.name for s in FINAL_ANSWER])
async def test_fa1_the_answer_streams_live_before_its_response_is_journaled(scenario: golden_support.Scenario) -> None:
    doc = await record(scenario)
    response = [e for e in doc["events"] if e["type"] == "model.response"][-1]
    answer = str(_finalized_raw(doc)["answer"])
    streamed = [f for f in doc["live"] if f["frame"]["kind"] == "text_delta"]
    assert "".join(str(f["frame"]["data"]["text"]) for f in streamed).endswith(answer)
    assert all(f["afterSeq"] < response["seq"] for f in streamed)


def _finalized_raw(doc: dict[str, object]) -> dict[str, object]:
    events = doc["events"]
    assert isinstance(events, list)
    return next(e["data"] for e in events if e["type"] == "x.pipeshub.answer.finalized")


@pytest.mark.parametrize("scenario", FINAL_ANSWER, ids=[s.name for s in FINAL_ANSWER])
async def test_fa3_the_answer_and_its_confidence_are_legacys(scenario: golden_support.Scenario) -> None:
    sse = await golden_support.record_stream(scenario)
    expected = _final_snapshot(sse)
    events, _ = await run_durably(scenario)
    finalized = _finalized(events)
    assert (finalized["answer"], finalized["confidence"]) == (expected["answer"], expected["confidence"])
    assert expected["confidence"] == "High"


async def test_fa4_a_batch_resumed_after_its_answer_completes_with_it_after_the_write() -> None:
    """The worker dies once the batch's reads (the answer) are committed and
    before the write runs; the next attempt runs the write and completes with
    the journaled answer instead of asking the model again."""
    whole, _ = await run_durably(WITH_WRITE)
    transports: list[ScriptedTransport] = []
    # Commits: the pre-run (with the capability record), the response, then the reads.
    resumed, _ = await run_durably(WITH_WRITE, crash_at="worker.commit.after_ack", crash_on=3, transports=transports)
    types = [e.type for e in resumed]
    assert types.count("run.claimed") == 2
    assert types.count("model.response") == 1, "the model is not asked again"
    assert len(transports[0].calls) == 1
    outcomes = [e for e in resumed if e.type == "tool.outcome"]
    assert [e.data["invocationId"] for e in outcomes] == ["t1.1", "t1.0"], "the answer, then the write"
    assert types[-1] == "run.completed"
    assert _finalized(resumed) == _finalized(whole)
