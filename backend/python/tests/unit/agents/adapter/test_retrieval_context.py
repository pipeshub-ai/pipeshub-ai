"""Opt-in `retrieval_context` stream frames: wire payload, both formatters,
the delta ledger, and `emit_retrieval_context` gating."""

from __future__ import annotations

import asyncio
from typing import Any, get_args
from unittest.mock import patch

import pytest

from app.agents.agent_loop.protocol.formatter import AGUIFormatter, LegacyFormatter
from app.agents.agent_loop.protocol.retrieval_context import (
    FetchedRangePayload,
    RetrievalContextPayload,
    RetrievedRecordPayload,
)
from app.agents.agent_loop.retrieval_ledger import (
    MAX_BLOCK_INDICES_PER_RECORD,
    MAX_FRAMES_PER_RUN,
    MAX_KNOWN_RECORD_IDS_PER_FRAME,
    MAX_RECORDS_PER_FRAME,
    RetrievalContextLedger,
    emit_retrieval_context,
)
from app.modules.retrieval.provenance import note_retrieved_by
from app.utils.render_budget import TruncatedBy
from tests.unit.agents.adapter.conftest import make_context


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write(self, event: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        self.events.append(event)


def _block(vrid: str, index: int | None, score: float = 0.5, record_id: str = "") -> dict[str, Any]:
    return {
        "virtual_record_id": vrid,
        "block_index": index,
        "score": score,
        "metadata": {"recordId": record_id or f"rec-{vrid}", "recordName": f"name-{vrid}"},
    }


def _state(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    state: dict[str, Any] = {
        "final_results": [],
        "virtual_record_id_to_result": {},
        "known_record_ids": set(),
    }
    state.update(overrides)
    return state


def _payload() -> RetrievalContextPayload:
    return RetrievalContextPayload(
        seq=1,
        source="tool",
        toolName="knowledgegraph__search",
        toolCallId="call-1",
        records=[
            RetrievedRecordPayload(
                virtualRecordId="vr-1", recordId="rec-1", blockIndices=[3],
                fetched=[FetchedRangePayload(startBlock=0, blocksRendered=5, complete=True)],
            )
        ],
        cumulativeBlocks=1,
        cumulativeRecords=1,
    )


class TestWirePayload:
    def test_wire_keys_are_pinned(self) -> None:
        wire = _payload().to_wire_dict()

        assert set(wire) == {
            "schemaVersion", "seq", "source", "status", "toolName", "toolCallId",
            "records", "knownRecordIds", "cumulativeBlocks", "cumulativeRecords",
        }
        assert set(wire["records"][0]) == {
            "virtualRecordId", "recordId", "blockIndices", "summaryHit", "fetched",
        }
        assert wire["records"][0]["fetched"] == [
            {"startBlock": 0, "blocksRendered": 5, "complete": True}
        ]

    def test_extra_fields_are_rejected(self) -> None:
        try:
            RetrievalContextPayload(seq=1, source="tool", bogus=1)  # type: ignore[call-arg]
        except ValueError:
            return
        raise AssertionError("extra field accepted")


class TestFormatters:
    def test_legacy_shape(self) -> None:
        frames = LegacyFormatter().retrieval_context(make_context(), payload=_payload())

        assert frames == [{"event": "retrieval_context", "data": _payload().to_wire_dict()}]

    def test_agui_wraps_in_custom_event(self) -> None:
        context = make_context(protocol="agui", run_id="run-1")

        frames = AGUIFormatter().retrieval_context(context, payload=_payload())

        assert len(frames) == 1
        assert frames[0]["event"] == "CUSTOM"
        assert frames[0]["data"]["name"] == "retrieval_context"
        assert frames[0]["data"]["runId"] == "run-1"
        assert frames[0]["data"]["value"] == _payload().to_wire_dict()


class TestLedger:
    def test_reports_new_blocks_grouped_by_record(self) -> None:
        ledger = RetrievalContextLedger()
        state = _state(final_results=[_block("vr-1", 1, 0.2), _block("vr-1", 4, 0.9), _block("vr-2", 0)])

        delta = ledger.take_delta(state)

        by_vrid = {r.virtualRecordId: r for r in delta.records}
        assert by_vrid["vr-1"].blockIndices == [1, 4]
        assert by_vrid["vr-1"].maxScore == 0.9
        assert by_vrid["vr-1"].recordId == "rec-vr-1"
        assert by_vrid["vr-2"].blockIndices == [0]
        assert delta.cumulative_blocks == 3
        assert delta.cumulative_records == 2

    def test_second_delta_contains_only_new_items(self) -> None:
        ledger = RetrievalContextLedger()
        state = _state(final_results=[_block("vr-1", 1)])
        ledger.take_delta(state)
        state["final_results"] = [_block("vr-1", 1), _block("vr-1", 2)]

        delta = ledger.take_delta(state)

        assert [r.blockIndices for r in delta.records] == [[2]]
        assert delta.cumulative_blocks == 2

    def test_unchanged_state_yields_empty_delta(self) -> None:
        ledger = RetrievalContextLedger()
        state = _state(final_results=[_block("vr-1", 1)])
        ledger.take_delta(state)

        assert ledger.take_delta(state).is_empty

    def test_a_tables_matched_rows_are_all_reported(self) -> None:
        """A table unit's own index is its first row; the other rows the model
        read are children, and a trace without them looks like the model cited
        rows it was never shown."""
        table = {
            **_block("vr-1", 3), "block_type": "table",
            "content": ("Countries by area", [_block("vr-1", 3), _block("vr-1", 9), _block("vr-1", 12)]),
        }
        ledger = RetrievalContextLedger()

        delta = ledger.take_delta(_state(final_results=[table]))

        assert delta.records[0].blockIndices == [3, 9, 12]
        assert delta.records[0].summaryHit is False
        assert delta.cumulative_blocks == 3
        assert ledger.take_delta(_state(final_results=[table, _block("vr-1", 9)])).is_empty

    def test_summary_hit_has_no_block_index(self) -> None:
        delta = RetrievalContextLedger().take_delta(_state(final_results=[_block("vr-1", None)]))

        assert delta.records[0].summaryHit is True
        assert delta.records[0].blockIndices == []

    def test_record_identity_prefers_record_map(self) -> None:
        state = _state(
            final_results=[_block("vr-1", 0, record_id="meta-id")],
            virtual_record_id_to_result={
                "vr-1": {"id": "map-id", "record_name": "Marie_Curie", "weburl": "/record/map-id"},
            },
        )

        record = RetrievalContextLedger().take_delta(state).records[0]

        assert (record.recordId, record.recordName, record.webUrl) == (
            "map-id", "Marie_Curie", "/record/map-id",
        )

    def test_replaced_record_map_reports_records_without_blocks(self) -> None:
        ledger = RetrievalContextLedger()
        state = _state(final_results=[_block("vr-1", 0)])
        ledger.take_delta(state)
        state["virtual_record_id_to_result"] = {"vr-1": {"id": "r1"}, "vr-9": {"id": "r9"}}

        delta = ledger.take_delta(state)

        assert [(r.virtualRecordId, r.recordId, r.blockIndices) for r in delta.records] == [
            ("vr-9", "r9", []),
        ]

    def test_known_record_ids_reported_once_sorted(self) -> None:
        ledger = RetrievalContextLedger()
        state = _state(known_record_ids={"b", "a"})

        first = ledger.take_delta(state)
        state["known_record_ids"] = {"a", "b", "c"}
        second = ledger.take_delta(state)

        assert first.known_record_ids == ["a", "b"]
        assert second.known_record_ids == ["c"]

    def test_fetch_outcomes_attach_to_record(self) -> None:
        ledger = RetrievalContextLedger()
        outcome = {"virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 12, "complete": False}
        state = _state(fetch_render_outcomes={"rec-7": [outcome]})

        first = ledger.take_delta(state)
        state["fetch_render_outcomes"]["rec-7"].append(
            {"virtualRecordId": "vr-7", "startBlock": 12, "blocksRendered": 8, "complete": True}
        )
        second = ledger.take_delta(state)

        assert first.records[0].virtualRecordId == "vr-7"
        assert first.records[0].recordId == "rec-7"
        assert first.records[0].fetched == [
            FetchedRangePayload(startBlock=0, blocksRendered=12, complete=False)
        ]
        assert [f.startBlock for f in second.records[0].fetched] == [12]

    def test_a_record_found_by_several_layers_lists_each_once(self) -> None:
        ledger = RetrievalContextLedger()
        state = _state(include_retrieval_context=True, final_results=[_block("vr-1", 0)])
        note_retrieved_by(state, "vector", [("vr-1", "rec-vr-1", None)])
        note_retrieved_by(state, "pattern_match", [("vr-1", None, None)])

        first = ledger.take_delta(state)
        note_retrieved_by(state, "entity", [("vr-1", None, None)])
        second = ledger.take_delta(state)

        assert first.records[0].retrievedBy == ["vector", "pattern_match"]
        assert second.records[0].retrievedBy == ["entity"]
        assert second.records[0].blockIndices == []
        assert ledger.take_delta(state).is_empty

    def test_a_record_named_without_content_is_reported_with_its_identity(self) -> None:
        state = _state(include_retrieval_context=True)
        note_retrieved_by(state, "pattern_match", [("vr-9", "rec-9", "Runbook")])

        (record,) = RetrievalContextLedger().take_delta(state).records

        assert (record.recordId, record.recordName) == ("rec-9", "Runbook")
        assert record.blockIndices == [] and record.fetched == []
        assert record.retrievedBy == ["pattern_match"]

    def test_a_fetch_reports_the_exact_blocks_it_showed(self) -> None:
        outcome = {
            "virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 2, "complete": False,
            "shownBlocks": [40, 41, 90],
            "tableTruncation": {"blockGroupIndex": 3, "rowsShown": 12, "rowsTotal": 500},
        }

        delta = RetrievalContextLedger().take_delta(_state(fetch_render_outcomes={"rec-7": [outcome]}))

        [fetched] = delta.records[0].fetched
        assert fetched.shownBlocks == [40, 41, 90]
        assert fetched.tableTruncation.model_dump() == {"blockGroupIndex": 3, "rowsShown": 12, "rowsTotal": 500}

    def test_why_a_fetch_stopped_and_what_the_model_asked_for_are_reported(self) -> None:
        outcome = {
            "virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 50, "complete": False,
            "truncatedBy": "blocks", "requestedMaxBlocks": 50, "startBlockSent": True,
        }

        delta = RetrievalContextLedger().take_delta(_state(fetch_render_outcomes={"rec-7": [outcome]}))

        [fetched] = delta.records[0].fetched
        fetched = fetched.model_dump(exclude_none=True)
        assert fetched["truncatedBy"] == "blocks"
        assert fetched["requestedMaxBlocks"] == 50
        assert fetched["startBlockSent"] is True

    def test_an_entry_without_the_newer_fields_keeps_its_old_wire_shape(self) -> None:
        outcome = {"virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 3, "complete": True}

        delta = RetrievalContextLedger().take_delta(_state(fetch_render_outcomes={"rec-7": [outcome]}))

        [fetched] = delta.records[0].fetched
        assert set(fetched.model_dump(exclude_none=True)) == {"startBlock", "blocksRendered", "complete"}

    @pytest.mark.parametrize("reason", get_args(TruncatedBy))
    def test_every_reason_the_renderer_reports_reaches_the_frame(self, reason: str) -> None:
        outcome = {"virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 1, "complete": False,
                   "truncatedBy": reason}

        delta = RetrievalContextLedger().take_delta(_state(fetch_render_outcomes={"rec-7": [outcome]}))

        assert delta.records[0].fetched[0].truncatedBy == reason

    def test_an_unknown_stop_reason_is_dropped_not_fatal(self) -> None:
        outcome = {"virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 3, "complete": False,
                   "truncatedBy": "weather", "requestedMaxBlocks": "50", "startBlockSent": "yes"}

        delta = RetrievalContextLedger().take_delta(_state(fetch_render_outcomes={"rec-7": [outcome]}))

        [fetched] = delta.records[0].fetched
        assert (fetched.truncatedBy, fetched.requestedMaxBlocks, fetched.startBlockSent) == (None, None, None)

    def test_two_selections_with_the_same_unit_count_are_both_reported(self) -> None:
        first = {"virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 3, "complete": False,
                 "shownBlocks": [1, 2, 3]}
        second = {**first, "shownBlocks": [7, 8, 9]}
        ledger = RetrievalContextLedger()

        ledger.take_delta(_state(fetch_render_outcomes={"rec-7": [first]}))
        delta = ledger.take_delta(_state(fetch_render_outcomes={"rec-7": [first, second]}))

        assert [f.shownBlocks for f in delta.records[0].fetched] == [[7, 8, 9]]

    def test_a_malformed_fetch_entry_costs_only_itself(self) -> None:
        good = {"virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 3, "complete": True}
        state = _state(
            final_results=[_block("vr-1", 0)],
            fetch_render_outcomes={"rec-7": ["not an outcome", None, good]},
        )

        delta = RetrievalContextLedger().take_delta(state)

        by_id = {r.virtualRecordId: r for r in delta.records}
        assert by_id["vr-1"].blockIndices == [0]
        assert [f.blocksRendered for f in by_id["vr-7"].fetched] == [3]

    def test_a_fetch_is_reported_as_a_layer(self) -> None:
        outcome = {"virtualRecordId": "vr-7", "startBlock": 0, "blocksRendered": 3, "complete": True}
        delta = RetrievalContextLedger().take_delta(_state(fetch_render_outcomes={"rec-7": [outcome]}))

        assert delta.records[0].retrievedBy == ["fetch"]

    def test_records_without_provenance_leave_the_key_off_the_wire(self) -> None:
        delta = RetrievalContextLedger().take_delta(_state(final_results=[_block("vr-1", 0)]))

        assert "retrievedBy" not in delta.records[0].model_dump(exclude_none=True)

    def test_malformed_entries_are_skipped(self) -> None:
        state = _state(final_results=["junk", {"block_index": 1}, _block("vr-1", 2)])

        delta = RetrievalContextLedger().take_delta(state)

        assert [r.virtualRecordId for r in delta.records] == ["vr-1"]


class TestEmitRetrievalContext:
    def _context(self, *, enabled: bool = True) -> Any:  # noqa: ANN401
        return make_context(
            protocol="agui", run_id="run-1", include_retrieval_context=enabled,
            event_sink=_RecordingSink(),
        )

    async def test_flag_off_emits_nothing(self) -> None:
        context = self._context(enabled=False)
        context.tool_state["final_results"] = [_block("vr-1", 0)]

        await emit_retrieval_context(context, source="prefetch", always=True)

        assert context.event_sink.events == []

    async def test_ok_with_empty_delta_emits_nothing(self) -> None:
        context = self._context()

        await emit_retrieval_context(context, source="tool", tool_name="x")

        assert context.event_sink.events == []

    async def test_always_emits_even_when_empty(self) -> None:
        context = self._context()

        await emit_retrieval_context(
            context, source="prefetch", status="skipped", status_reason="followup", always=True,
        )

        value = context.event_sink.events[0]["data"]["value"]
        assert value["status"] == "skipped"
        assert value["statusReason"] == "followup"
        assert value["records"] == []

    async def test_error_status_is_emitted_and_message_truncated(self) -> None:
        context = self._context()

        await emit_retrieval_context(
            context, source="tool", status="error", tool_name="retrieval__search_internal_knowledge",
            error_message="x" * 2000,
        )

        value = context.event_sink.events[0]["data"]["value"]
        assert value["status"] == "error"
        assert len(value["errorMessage"]) == 500

    async def test_seq_increments_across_frames(self) -> None:
        context = self._context()
        context.tool_state["final_results"] = [_block("vr-1", 0)]
        await emit_retrieval_context(context, source="prefetch")
        context.tool_state["final_results"].append(_block("vr-2", 0))
        await emit_retrieval_context(context, source="tool", tool_call_id="c1")

        seqs = [e["data"]["value"]["seq"] for e in context.event_sink.events]
        assert seqs == [1, 2]

    async def test_concurrent_emits_never_double_report(self) -> None:
        context = self._context()
        context.tool_state["final_results"] = [_block("vr-1", i) for i in range(20)]

        await asyncio.gather(*[
            emit_retrieval_context(context, source="tool", tool_call_id=f"c{i}") for i in range(5)
        ])

        reported = [
            (record["virtualRecordId"], index)
            for event in context.event_sink.events
            for record in event["data"]["value"]["records"]
            for index in record["blockIndices"]
        ]
        assert len(reported) == 20
        assert len(set(reported)) == 20


class TestFrameBounds:
    """One oversized result must not turn a frame, or a run's trace, into
    megabytes of ids; a frame that left something out says so."""

    def _context(self) -> Any:  # noqa: ANN401
        return make_context(
            protocol="agui", run_id="run-1", include_retrieval_context=True,
            event_sink=_RecordingSink(),
        )

    @staticmethod
    def _values(context: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return [event["data"]["value"] for event in context.event_sink.events]

    async def test_a_frame_within_the_caps_carries_no_truncated_key(self) -> None:
        context = self._context()
        context.tool_state["final_results"] = [_block("vr-1", 0)]

        await emit_retrieval_context(context, source="tool")

        assert "truncated" not in self._values(context)[0]

    async def test_records_past_the_cap_are_left_out_and_flagged(self) -> None:
        context = self._context()
        context.tool_state["final_results"] = [
            _block(f"vr-{i}", 0) for i in range(MAX_RECORDS_PER_FRAME + 5)
        ]

        await emit_retrieval_context(context, source="tool")

        [value] = self._values(context)
        assert len(value["records"]) == MAX_RECORDS_PER_FRAME
        assert value["truncated"] is True
        assert value["cumulativeRecords"] == MAX_RECORDS_PER_FRAME + 5

    async def test_a_huge_table_is_cut_to_the_block_cap(self) -> None:
        context = self._context()
        rows = [{"block_index": i} for i in range(1, MAX_BLOCK_INDICES_PER_RECORD + 50)]
        context.tool_state["final_results"] = [
            {"virtual_record_id": "vr-t", "block_index": 0, "content": ("summary", rows)},
        ]

        await emit_retrieval_context(context, source="tool")

        [value] = self._values(context)
        assert len(value["records"][0]["blockIndices"]) == MAX_BLOCK_INDICES_PER_RECORD
        assert value["truncated"] is True

    async def test_a_huge_fetched_table_is_cut_to_the_block_cap(self) -> None:
        context = self._context()
        context.tool_state["fetch_render_outcomes"] = {"rec-t": [{
            "virtualRecordId": "vr-t", "startBlock": 0, "blocksRendered": 1, "complete": True,
            "shownBlocks": list(range(MAX_BLOCK_INDICES_PER_RECORD + 10)),
        }]}

        await emit_retrieval_context(context, source="tool")

        [value] = self._values(context)
        assert len(value["records"][0]["fetched"][0]["shownBlocks"]) == MAX_BLOCK_INDICES_PER_RECORD
        assert value["truncated"] is True

    async def test_known_ids_past_the_cap_are_left_out_and_flagged(self) -> None:
        context = self._context()
        context.tool_state["known_record_ids"] = {
            f"rec-{i:05d}" for i in range(MAX_KNOWN_RECORD_IDS_PER_FRAME + 1)
        }

        await emit_retrieval_context(context, source="tool")

        [value] = self._values(context)
        assert len(value["knownRecordIds"]) == MAX_KNOWN_RECORD_IDS_PER_FRAME
        assert value["truncated"] is True

    async def test_the_run_stops_sending_frames_at_the_cap(self) -> None:
        context = self._context()
        for i in range(MAX_FRAMES_PER_RUN + 3):
            await emit_retrieval_context(
                context, source="tool", status="empty", tool_call_id=f"c{i}",
            )

        values = self._values(context)
        assert len(values) == MAX_FRAMES_PER_RUN
        assert values[-1]["seq"] == MAX_FRAMES_PER_RUN
        assert values[-1]["truncated"] is True
        assert "truncated" not in values[-2]


class TestEmitNeverRaises:
    """It runs after every tool call, before the answer is finalized."""

    async def test_a_ledger_fault_drops_the_frame(self) -> None:
        context = TestFrameBounds()._context()
        context.tool_state["final_results"] = [_block("vr-1", 0)]

        with patch.object(RetrievalContextLedger, "take_delta", side_effect=RuntimeError("bug")):
            await emit_retrieval_context(context, source="tool")

        assert context.event_sink.events == []

    async def test_a_formatter_fault_drops_the_frame(self) -> None:
        context = TestFrameBounds()._context()
        context.tool_state["final_results"] = [_block("vr-1", 0)]

        with patch.object(
            type(context.formatter), "retrieval_context", side_effect=ValueError("bad frame"),
        ):
            await emit_retrieval_context(context, source="tool")

        assert context.event_sink.events == []

    async def test_a_failing_stream_write_drops_the_frame(self) -> None:
        class _BrokenSink:
            async def write(self, _event: dict[str, Any]) -> None:
                raise ConnectionResetError("client went away")

        context = make_context(
            protocol="agui", run_id="run-1", include_retrieval_context=True, event_sink=_BrokenSink(),
        )
        context.tool_state["final_results"] = [_block("vr-1", 0)]

        await emit_retrieval_context(context, source="tool")

    async def test_cancellation_is_not_swallowed(self) -> None:
        context = TestFrameBounds()._context()

        with patch.object(
            RetrievalContextLedger, "take_delta", side_effect=asyncio.CancelledError,
        ):
            try:
                await emit_retrieval_context(context, source="tool")
            except asyncio.CancelledError:
                return
        raise AssertionError("CancelledError was swallowed")
