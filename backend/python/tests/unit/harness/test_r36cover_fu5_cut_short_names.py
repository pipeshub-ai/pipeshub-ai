"""R36COVER-FU-5: a loop run its budget cut before any answer named what it had gathered by tool id
("What it had gathered so far: - artifacts__save_artifact"), not the file it saved or what it found. Its no-model
answer now names the files it saved and the pages it read, as an engine job's cut does (the presenter's own
``_sources``, one wording for both), and each tool by a plain label, never its id (the activity rule).
"""

from __future__ import annotations

from typing import Any, cast

from app.harness.artifacts.events import PRESENTED, presented_names
from harness_core.engines.outcome.deliverable import ANSWER
from harness_core.engines.outcome.ledger import OutcomeLedger, SourceGathered
from harness_core.engines.outcome.loop import loop_settled
from harness_core.engines.outcome.presenter import PRESENTER, plain_tool_label
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.turn_limit import cut_short_fallback
from harness_core.kernel.durable.ports import RunEnding

RUN = "run-fu5"
CALLS = (
    ToolCall(id="c1", name="web_search", arguments={}),
    ToolCall(id="c2", name="artifacts__save_artifact", arguments={}),
)


def _presented(name: str, run_id: str = RUN, seq: int = 5) -> JournalEvent:
    return JournalEvent(
        org_id="o", session_id="s", stream_id="s", seq=seq, run_id=run_id, type=PRESENTED, schema_version=1,
        critical=False, ts="2026-11-14T00:00:00.000Z", writer="worker", visibility="owner",
        data={"artifactId": f"a-{seq}", "fileName": name},
    )


def test_fu5_1_tools_are_named_by_a_plain_label_never_their_id() -> None:
    assert plain_tool_label("artifacts__save_artifact") == "Save artifact"
    assert plain_tool_label("web_search") == "Web search"
    assert plain_tool_label("mcp/jira__search-issues") == "Search issues"
    text = cut_short_fallback("budget", [c.name for c in CALLS])
    assert "artifacts__save_artifact" not in text and "- Save artifact" in text and "- Web search" in text


def test_fu5_2_the_no_model_answer_names_the_saved_file_and_the_pages_it_read() -> None:
    sources = (SourceGathered("https://a.example/q3", "web_page", "https://a.example/q3", "Q3 results"),)
    text = PRESENTER.no_answer("budget", [c.name for c in CALLS], saved=["q3-summary.md", "q3-summary.md"], sources=sources)
    assert PRESENTER.is_no_answer(text)
    assert "It saved **q3-summary.md** in this conversation's files." in text
    assert text.count("q3-summary.md") == 1
    assert "- [Q3 results](https://a.example/q3)" in text
    assert "__" not in text


def test_fu5_3_a_cut_loop_run_settles_with_its_saved_file_and_sources() -> None:
    ledger = OutcomeLedger()
    _ = ledger.record(SourceGathered("https://a.example/q3", "web_page", "https://a.example/q3", "Q3 results"))
    events = (_presented("q3-summary.md"), _presented("other.md", run_id="run-other", seq=6))
    answer = cut_short_fallback("budget", [c.name for c in CALLS])
    ending = RunEnding(outcome="answered", answer=answer, truncated_by="budget", tool_calls=CALLS, events=events)
    saved = presented_names(ending.events, RUN)
    assert saved == ("q3-summary.md",)
    settled = loop_settled(ending, RUN, ledger, ANSWER, saved=saved)
    assert settled is not None
    # R50JFIX-1: what it did is the prose (the answer every surface shows), the stop and the action the closing
    prose, closing = settled.framed.prose, settled.framed.closing
    assert prose.startswith("It saved **q3-summary.md**") and "[Q3 results](https://a.example/q3)" in prose
    assert "artifacts__save_artifact" not in prose and "other.md" not in prose
    assert "q3-summary.md" not in closing and closing.startswith("---")
    data = cast("dict[str, Any]", settled.event.data)
    assert data["presented"]["closing"] == closing
