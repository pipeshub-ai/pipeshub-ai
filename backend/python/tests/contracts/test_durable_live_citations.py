"""C-19: an answer cites a file a tool read live from a connector, on both paths.

`agent_live_file_citation` reads a Drive file live (never indexed) and cites
two of its blocks. Legacy's final answer carries both citations marked live
(C-18) with no record page; a durable run finalizes the same citations; a
worker that dies after the read's outcome is journaled, and the fresh worker
that re-claims the run, give the uncrashed run's citations without reading
the file again (PA-8).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest

from app.models.citation_source import is_live_record_id
from tests.contracts.golden_support import GOLDEN_DIR, Scenario
from tests.contracts.scenarios import SCENARIOS
from tests.contracts.test_durable_goldens import run_durably

if TYPE_CHECKING:
    from harness_core.journal import JournalEvent
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

GOLDEN = next(s for s in SCENARIOS if s.name == "agent_live_file_citation")


def _legacy_citations() -> list[dict[str, Any]]:
    final: dict[str, Any] = {}
    for line in (GOLDEN_DIR / f"{GOLDEN.name}.sse").read_text(encoding="utf-8").splitlines():
        if line.startswith("data: "):
            frame = json.loads(line[6:])
            if frame.get("type") == "STATE_SNAPSHOT" and (frame.get("snapshot") or {}).get("final"):
                final = frame["snapshot"]
    return list(final["citations"])


def _finalized(events: list[JournalEvent]) -> dict[str, Any]:
    [finalized] = [e.data for e in events if e.type == "x.pipeshub.answer.finalized"]
    return dict(finalized)


def test_cl1_legacy_cites_the_live_file_as_live_without_a_record_page() -> None:
    citations = _legacy_citations()
    assert [c["content"] for c in citations] == ["The Q3 launch moves to May.", "The Q3 budget is unchanged."]
    for citation in citations:
        meta = citation["metadata"]
        assert (meta["sourceKind"], meta["connector"], meta["externalRecordId"]) == ("live", "DRIVE", "file-q3")
        assert is_live_record_id(meta["recordId"])
        assert meta["previewRenderable"] is False
        assert "/record/" not in meta["webUrl"]


async def test_cl2_a_durable_run_finalizes_legacys_citations() -> None:
    events, _ = await run_durably(GOLDEN)
    assert _finalized(events)["citations"] == _legacy_citations()
    [recorded] = [e.data for e in events if e.type == "x.pipeshub.citations.recorded"]
    assert recorded["callId"] == "call-read"
    assert [r["id"] for r in recorded["toolRecords"]] == [_legacy_citations()[0]["metadata"]["recordId"]]
    assert [r["ref"] for r in recorded["refs"]] == ["ref1", "ref2"]


def _answer_twice(t: ScriptedTransport) -> None:
    # A worker lost before its response commit leaves the sample unjournaled: the next samples again.
    GOLDEN.script(t)
    t.add_text_chunks(["The Q3 launch moves to May [source](ref1) ", "and the budget is unchanged [source](ref2)."])


RESUMED = Scenario(GOLDEN.name, "agent", GOLDEN.chat_mode, GOLDEN.query, _answer_twice, toolsets=GOLDEN.toolsets)

# After the read's outcome (and its citation sources) are durable: the answer's
# sampling, its commit, and the commit acknowledgements that follow.
POINTS = [
    ("worker.model.before_response_commit", 2),
    ("worker.model.after_response_commit", 2),
    ("worker.commit.after_ack", 2),
    ("worker.commit.after_ack", 3),
]


@pytest.mark.parametrize(("point", "nth"), POINTS, ids=[f"{p}#{n}" for p, n in POINTS])
async def test_cl3_live_citations_survive_a_crash_and_a_reclaim(point: str, nth: int) -> None:
    clean, _ = await run_durably(RESUMED)
    events, _ = await run_durably(RESUMED, crash_at=point, crash_on=nth)

    types = Counter(e.type for e in events)
    assert [e.data["attempt"] for e in events if e.type == "run.claimed"] == [1, 2]
    assert types["run.completed"] == 1 and "run.failed" not in types
    assert types["tool.outcome"] == 1, "the file is not read again"
    assert types["x.pipeshub.citations.recorded"] == 1
    expected, actual = _finalized(clean), _finalized(events)
    assert actual["citations"] == expected["citations"] == _legacy_citations()
    assert actual["answer"] == expected["answer"]
