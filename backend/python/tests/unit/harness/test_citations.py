"""PA-8 unit cases: deltas, and a restore that keeps numbers and short ids."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.harness.citations import EVENT, CitationJournal
from app.utils.chat_helpers import CitationRefMapper, RecordIdShortener
from harness_core.journal import JournalEvent

if TYPE_CHECKING:
    import pytest


def _context(state: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
    return SimpleNamespace(tool_state=state if state is not None else {}, root_agent_spec=None)


def _event(seq: int, data: dict[str, Any]) -> JournalEvent:
    return JournalEvent(
        org_id="org-1", session_id="ses-1", stream_id="ses-1", seq=seq, run_id="run-1",
        attempt_id="att-1", fence=1, type=EVENT, schema_version=1, critical=False,
        ts="2026-10-01T12:00:00Z", writer="worker", visibility="owner", data=data,
    )


def test_ct1_deltas_carry_only_what_changed() -> None:
    mapper, shortener = CitationRefMapper(), RecordIdShortener()
    state: dict[str, Any] = {"citation_ref_mapper": mapper, "record_id_shortener": shortener}
    journal = CitationJournal(_context(state))
    assert journal.delta("c0") is None
    mapper.get_or_create_ref("https://x/1")
    shortener.get_or_create_short_id("record-aaaa")
    state["virtual_record_id_to_result"] = {"vr-1": {"id": "rec-1"}}
    state["known_record_ids"] = {"rec-1"}
    first = journal.delta("c1")
    assert first == {
        "callId": "c1",
        "refs": [{"url": "https://x/1", "ref": "ref1"}],
        "shortIds": [{"full": "record-aaaa", "short": "R1"}],
        "records": {"vr-1": {"id": "rec-1"}},
        "knownRecordIds": ["rec-1"],
    }
    mapper.get_or_create_ref("https://x/2")
    state["virtual_record_id_to_result"] = {"vr-1": {"id": "rec-1", "full": True}}
    second = journal.delta("c2")
    assert second == {
        "callId": "c2",
        "refs": [{"url": "https://x/2", "ref": "ref2"}],
        "records": {"vr-1": {"id": "rec-1", "full": True}},
    }


async def test_ct2_a_restore_keeps_numbers_short_ids_and_logs_the_rebuild(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    events = [
        _event(5, {"callId": "c1", "refs": [{"url": "https://x/1", "ref": "ref1"}, {"url": "https://x/2", "ref": "ref2"}],
                   "shortIds": [{"full": "record-aaaa", "short": "R1"}],
                   "records": {"vr-1": {"id": "rec-1"}},
                   "finalResults": [{"virtual_record_id": "vr-1", "block_index": 0, "content": "a"}],
                   "knownRecordIds": ["rec-1"]}),
    ]
    state: dict[str, Any] = {"record_id_shortener": RecordIdShortener()}
    journal = CitationJournal(_context(state))
    await journal.restore(events)
    mapper = state["citation_ref_mapper"]
    assert mapper.ref_to_url == {"ref1": "https://x/1", "ref2": "https://x/2"}
    assert mapper.get_or_create_ref("https://x/3") == "ref3", "numbering continues"
    assert state["record_id_shortener"].resolve("R1") == "record-aaaa"
    assert state["record_id_shortener"].get_or_create_short_id("record-bbbb") == "R2"
    assert state["virtual_record_id_to_result"] == {"vr-1": {"id": "rec-1"}}
    assert state["known_record_ids"] == {"rec-1"}
    # What was restored is not journaled again; only what comes after it.
    assert journal.delta("c2") == {"callId": "c2", "refs": [{"url": "https://x/3", "ref": "ref3"}], "shortIds": [{"full": "record-bbbb", "short": "R2"}]}
    rebuilt = [r for r in caplog.records if getattr(r, "harness_event", None) == "citations.rebuilt"]
    assert len(rebuilt) == 1
    assert rebuilt[0].harness["refs"] == 2 and rebuilt[0].harness["records"] == 1  # type: ignore[attr-defined]
    assert "https://x/1" not in rebuilt[0].getMessage()


async def test_ct3_a_resumed_attempt_keeps_the_id_recovery_list_and_fetch_selection() -> None:
    """SYNC3: main's search and fetch keep per-request state the model sees again (the ids an
    unresolved fetch points back at, in the order shown; the records already fetched with
    selection). A resumed attempt restores both as the first attempt left them."""
    from app.agents.actions.knowledge_graph.ops.fetch import FETCH_SELECTED_RECORDS_KEY
    from app.modules.agents.qna.chat_state import remember_record_ids

    state: dict[str, Any] = {}
    journal = CitationJournal(_context(state))
    remember_record_ids(state, ["rec-1", "rec-2"], names={"rec-1": "Plan", "rec-2": "Budget"})
    state.setdefault(FETCH_SELECTED_RECORDS_KEY, set()).add("rec-1")
    first = journal.delta("c1")
    remember_record_ids(state, ["rec-2"])  # re-shown: moves to the newest end
    second = journal.delta("c2")
    assert first is not None and second is not None
    assert second["knownRecordNames"] == [{"id": "rec-1", "name": "Plan"}, {"id": "rec-2", "name": "Budget"}]
    assert "fetchSelectedRecords" not in second

    resumed: dict[str, Any] = {}
    other = CitationJournal(_context(resumed))
    await other.restore([_event(5, dict(first)), _event(6, dict(second))])
    assert list(resumed["known_record_names"].items()) == list(state["known_record_names"].items())
    assert resumed[FETCH_SELECTED_RECORDS_KEY] == {"rec-1"}
    assert other.delta("c3") is None, "what was restored is not journaled again"


async def test_ct4_a_resumed_attempt_counts_only_the_nudges_the_run_journaled() -> None:
    """A continued turn is not a nudge: the gate's budget comes from the journaled nudge texts."""
    import hashlib

    from app.agents.agent_loop.hooks.completion_gate import _EMPTY_RESPONSE_NUDGE

    def message(seq: int, text: str) -> JournalEvent:
        digest = f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
        return _event(seq, {"role": "user", "textRef": {"hash": digest}, "isFinal": False, "injected": True}).model_copy(
            update={"type": "message.completed"}
        )

    context = SimpleNamespace(tool_state={}, root_agent_spec=None, completion_gate_nudges=0)
    await CitationJournal(context).restore([message(3, _EMPTY_RESPONSE_NUDGE), message(4, "a deadline note")])
    assert context.completion_gate_nudges == 1
