"""Opt-in `retrieval_context` stream frames: wire payload, both formatters,
the delta ledger, and `emit_retrieval_context` gating."""

from __future__ import annotations

import asyncio
from typing import Any

from app.agents.agent_loop.protocol.formatter import AGUIFormatter, LegacyFormatter
from app.agents.agent_loop.protocol.retrieval_context import (
    FetchedRangePayload,
    RetrievalContextPayload,
    RetrievedRecordPayload,
)
from app.agents.agent_loop.retrieval_ledger import (
    RetrievalContextLedger,
    emit_retrieval_context,
)
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
