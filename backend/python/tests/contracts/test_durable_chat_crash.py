"""PC-4: a plain-chat run survives a worker dying at each commit point it passes.

The built-in assistant's run (`chat_answer_with_citations`: the upfront
retrieval as a pre-run step, then one streamed answer) is killed at each
failpoint, its lease lapses and a fresh assembler (another worker's process
state) re-claims it. The K-12 invariants hold: it ends once, answered, with
the uncrashed run's answer and citations; the retrieval ran on the first
attempt only when its pre-run commit landed; the journal holds one goal, one
citation-source record and one final answer.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from tests.contracts.golden_support import Scenario
from tests.contracts.scenarios import SCENARIOS
from tests.contracts.test_durable_goldens import run_durably

if TYPE_CHECKING:
    from harness_core.journal import JournalEvent
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

GOLDEN = next(s for s in SCENARIOS if s.name == "chat_answer_with_citations")


def _answer_twice(t: ScriptedTransport) -> None:
    # A worker lost before its response commit leaves the sample unjournaled: the next samples again.
    GOLDEN.script(t)
    GOLDEN.script(t)


SCENARIO = Scenario(GOLDEN.name, "chat", GOLDEN.chat_mode, GOLDEN.query, _answer_twice)

# (failpoint, nth): the pre-run commit (goal, citation sources) is the first
# commit of the run, so every point is after it.
POINTS = [
    ("worker.prerun.after_commit", 1),
    ("worker.commit.after_ack", 1),
    ("worker.model.before_response_commit", 1),
    ("worker.model.after_response_commit", 1),
    ("worker.commit.after_ack", 2),
]


async def _run(**kwargs: Any) -> tuple[list[JournalEvent], int]:  # noqa: ANN401
    from app.agents.chat_modes import setup

    searches = 0
    real = setup.prefetch_retrieval

    async def counted(**prefetch: Any) -> Any:  # noqa: ANN401
        nonlocal searches
        searches += 1
        return await real(**prefetch)

    with patch("app.agents.chat_modes.setup.prefetch_retrieval", new=counted):
        events, _content = await run_durably(SCENARIO, **kwargs)
    return events, searches


def _final(events: list[JournalEvent]) -> dict[str, Any]:
    [finalized] = [e.data for e in events if e.type == "x.pipeshub.answer.finalized"]
    return {k: finalized[k] for k in ("outcome", "answer", "citations", "partTexts")}


@pytest.mark.parametrize(("point", "nth"), POINTS, ids=[f"{p}#{n}" for p, n in POINTS])
async def test_pc4_a_plain_chat_run_resumes_to_the_uncrashed_answer(point: str, nth: int) -> None:
    clean, clean_searches = await _run()
    events, searches = await _run(crash_at=point, crash_on=nth)

    types = Counter(e.type for e in events)
    assert types["run.completed"] == 1 and "run.failed" not in types and "run.quarantined" not in types
    assert types["x.pipeshub.answer.finalized"] == 1
    assert types["x.pipeshub.goal.presented"] == 1
    assert types["x.pipeshub.citations.recorded"] == 1
    assert [e.data["attempt"] for e in events if e.type == "run.claimed"] == [1, 2]
    assert _final(events) == _final(clean)
    assert len(_final(events)["citations"]) == 2
    # The retrieval is a pre-run step: journaled once, never searched again once it landed.
    assert clean_searches == 1
    assert searches == 1
