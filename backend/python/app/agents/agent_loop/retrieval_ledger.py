"""Turns the retrieval accumulators in `AgentContext.tool_state` into opt-in
CUSTOM `retrieval_context` stream frames.

Retrieval tools mutate (or replace) `final_results`,
`virtual_record_id_to_result` and `known_record_ids`; `fetch_record` also
records `fetch_render_outcomes`, and search, prefetch and the entity tools
note which layer found each record (`retrieved_by`), when the flag is on.
Instead of teaching every
tool to report, the ledger diffs that shared state after prefetch and after
every tool call and reports only what is new — a future retrieval tool is
covered without touching this module.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_args

from app.agents.agent_loop.protocol.retrieval_context import (
    FetchedRangePayload,
    FetchTruncatedBy,
    RetrievalContextPayload,
    RetrievalSource,
    RetrievalStatus,
    RetrievedRecordPayload,
    TableTruncationPayload,
)
from app.modules.retrieval.context.units import unit_block_indices
from app.modules.retrieval.provenance import (
    RETRIEVED_BY_KEY,
    RecordProvenance,
    RetrievalLayer,
)

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.chat_modes.prefetch import PrefetchResult

_MAX_ERROR_MESSAGE_CHARS = 500
# Size caps so one oversized result (a 10,000-row table, a listing of every
# record) cannot turn a frame, or the run's trace, into megabytes of ids.
MAX_RECORDS_PER_FRAME = 200
MAX_BLOCK_INDICES_PER_RECORD = 1_000
MAX_KNOWN_RECORD_IDS_PER_FRAME = 500
MAX_FRAMES_PER_RUN = 500

logger = logging.getLogger(__name__)

BlockKey = tuple[str, int | None]
FetchKey = tuple[str, int, int, tuple[int, ...]]


@dataclass
class _RecordDelta:
    virtual_record_id: str
    record_id: str | None = None
    record_name: str | None = None
    web_url: str | None = None
    block_indices: list[int] = field(default_factory=list)
    summary_hit: bool = False
    max_score: float | None = None
    fetched: list[FetchedRangePayload] = field(default_factory=list)
    retrieved_by: list[RetrievalLayer] = field(default_factory=list)

    def observe_score(self, score: object) -> None:
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            value = float(score)
            self.max_score = value if self.max_score is None else max(self.max_score, value)

    def to_payload(self) -> RetrievedRecordPayload:
        return RetrievedRecordPayload(
            virtualRecordId=self.virtual_record_id,
            recordId=self.record_id,
            recordName=self.record_name,
            webUrl=self.web_url,
            blockIndices=self.block_indices,
            summaryHit=self.summary_hit,
            maxScore=self.max_score,
            fetched=self.fetched,
            retrievedBy=self.retrieved_by or None,
        )


@dataclass(frozen=True)
class RetrievalDelta:
    records: list[RetrievedRecordPayload]
    known_record_ids: list[str]
    cumulative_blocks: int
    cumulative_records: int

    @property
    def is_empty(self) -> bool:
        return not self.records and not self.known_record_ids


def _str_or_none(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _int_or_none(value: object) -> int | None:
    """An int that is not a bool, else None: entries come from tool state."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


_block_index = _int_or_none


class RetrievalContextLedger:
    """Remembers what was already reported for one request.

    `take_delta` diffs and marks in a single synchronous pass (no `await`),
    so tool calls running concurrently in one asyncio wave can never report
    the same block twice.
    """

    def __init__(self) -> None:
        self._seen_blocks: set[BlockKey] = set()
        self._seen_records: set[str] = set()
        self._seen_known: set[str] = set()
        self._seen_fetches: set[FetchKey] = set()
        self._seen_layers: set[tuple[str, str]] = set()
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @property
    def frames_exhausted(self) -> bool:
        """The run already sent `MAX_FRAMES_PER_RUN` frames (the last one
        flagged `truncated`)."""
        return self._seq >= MAX_FRAMES_PER_RUN

    def take_delta(self, tool_state: Mapping[str, Any]) -> RetrievalDelta:
        records_map: Mapping[str, Any] = tool_state.get("virtual_record_id_to_result") or {}
        deltas: dict[str, _RecordDelta] = {}
        self._collect_blocks(tool_state.get("final_results") or [], records_map, deltas)
        self._collect_fetches(tool_state.get("fetch_render_outcomes") or {}, records_map, deltas)
        self._collect_provenance(tool_state.get(RETRIEVED_BY_KEY) or {}, records_map, deltas)
        self._collect_records(records_map, deltas)
        known = self._collect_known(tool_state.get("known_record_ids") or ())
        return RetrievalDelta(
            records=[delta.to_payload() for delta in deltas.values()],
            known_record_ids=known,
            cumulative_blocks=len(self._seen_blocks),
            cumulative_records=len(self._seen_records),
        )

    def _delta_for(
        self,
        virtual_record_id: str,
        records_map: Mapping[str, Any],
        deltas: dict[str, _RecordDelta],
        metadata: Mapping[str, Any] | None = None,
    ) -> _RecordDelta:
        existing = deltas.get(virtual_record_id)
        if existing is not None:
            return existing
        record = records_map.get(virtual_record_id) or {}
        meta = metadata or {}
        delta = _RecordDelta(
            virtual_record_id=virtual_record_id,
            record_id=_str_or_none(record.get("id") or meta.get("recordId")),
            record_name=_str_or_none(record.get("record_name") or meta.get("recordName")),
            web_url=_str_or_none(record.get("weburl") or meta.get("webUrl")),
        )
        deltas[virtual_record_id] = delta
        self._seen_records.add(virtual_record_id)
        return delta

    def _collect_blocks(
        self,
        blocks: Iterable[Any],
        records_map: Mapping[str, Any],
        deltas: dict[str, _RecordDelta],
    ) -> None:
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            virtual_record_id = _str_or_none(block.get("virtual_record_id"))
            if virtual_record_id is None:
                continue
            # A table or group unit renders its child rows too; recording only
            # the unit's own index hid every row after the first.
            indices: list[int | None] = list(dict.fromkeys(unit_block_indices(block))) or [None]
            keys: list[BlockKey] = [
                (virtual_record_id, index) for index in indices
                if (virtual_record_id, index) not in self._seen_blocks
            ]
            if not keys:
                continue
            self._seen_blocks.update(keys)
            metadata = block.get("metadata")
            delta = self._delta_for(
                virtual_record_id, records_map, deltas,
                metadata if isinstance(metadata, Mapping) else None,
            )
            for _vrid, index in keys:
                if index is None:
                    delta.summary_hit = True
                else:
                    delta.block_indices.append(index)
            delta.observe_score(block.get("score"))

    def _collect_fetches(
        self,
        outcomes: Mapping[str, Any],
        records_map: Mapping[str, Any],
        deltas: dict[str, _RecordDelta],
    ) -> None:
        for record_id, entries in outcomes.items():
            for entry in entries or ():
                if not isinstance(entry, Mapping):
                    continue
                shown = entry.get("shownBlocks")
                table = entry.get("tableTruncation")
                fetched = FetchedRangePayload(
                    startBlock=int(entry.get("startBlock") or 0),
                    blocksRendered=int(entry.get("blocksRendered") or 0),
                    complete=bool(entry.get("complete")),
                    shownBlocks=(
                        [i for i in shown if _block_index(i) is not None]
                        if isinstance(shown, list) else None
                    ),
                    tableTruncation=(
                        TableTruncationPayload.model_validate(table)
                        if isinstance(table, Mapping) else None
                    ),
                    truncatedBy=(
                        entry.get("truncatedBy")
                        if entry.get("truncatedBy") in get_args(FetchTruncatedBy) else None
                    ),
                    requestedMaxBlocks=_int_or_none(entry.get("requestedMaxBlocks")),
                    startBlockSent=(
                        entry.get("startBlockSent")
                        if isinstance(entry.get("startBlockSent"), bool) else None
                    ),
                )
                key: FetchKey = (
                    str(record_id), fetched.startBlock, fetched.blocksRendered,
                    tuple(fetched.shownBlocks or ()),
                )
                if key in self._seen_fetches:
                    continue
                self._seen_fetches.add(key)
                virtual_record_id = _str_or_none(entry.get("virtualRecordId")) or str(record_id)
                delta = self._delta_for(virtual_record_id, records_map, deltas)
                delta.record_id = delta.record_id or str(record_id)
                delta.fetched.append(fetched)
                self._observe_layer(delta, "fetch")

    def _collect_provenance(
        self,
        provenance: Mapping[str, RecordProvenance],
        records_map: Mapping[str, Any],
        deltas: dict[str, _RecordDelta],
    ) -> None:
        for virtual_record_id, entry in provenance.items():
            new_layers = [
                layer for layer in entry.layers
                if (virtual_record_id, layer) not in self._seen_layers
            ]
            if not new_layers:
                continue
            delta = self._delta_for(
                virtual_record_id, records_map, deltas,
                {"recordId": entry.record_id, "recordName": entry.record_name},
            )
            for layer in new_layers:
                self._observe_layer(delta, layer)

    def _observe_layer(self, delta: _RecordDelta, layer: RetrievalLayer) -> None:
        key = (delta.virtual_record_id, layer)
        if key not in self._seen_layers:
            self._seen_layers.add(key)
            delta.retrieved_by.append(layer)

    def _collect_records(
        self, records_map: Mapping[str, Any], deltas: dict[str, _RecordDelta],
    ) -> None:
        for virtual_record_id in list(records_map):
            if str(virtual_record_id) not in self._seen_records:
                self._delta_for(str(virtual_record_id), records_map, deltas)

    def _collect_known(self, record_ids: Iterable[Any]) -> list[str]:
        new_ids = sorted({str(rid) for rid in record_ids} - self._seen_known)
        self._seen_known.update(new_ids)
        return new_ids


async def emit_retrieval_context(
    context: AgentContext,
    *,
    source: RetrievalSource,
    status: RetrievalStatus = "ok",
    status_reason: str | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    error_message: str | None = None,
    always: bool = False,
) -> None:
    """Write one `retrieval_context` frame for whatever is new since the last
    frame. No-op unless the request opted in. An `ok` call with nothing new
    writes nothing (unless `always`), so ordinary tool calls cost no bytes.

    Never raises: it runs after every tool call and before the answer is
    finalized, so a fault here must cost a measurement, never the answer.
    """
    if not context.include_retrieval_context or context.event_sink is None:
        return
    try:
        ledger = context.retrieval_ledger
        if ledger.frames_exhausted:
            return
        delta = ledger.take_delta(context.tool_state)
        if delta.is_empty and status == "ok" and not always:
            return
        records, truncated = _bounded_records(delta.records)
        known_record_ids = delta.known_record_ids[:MAX_KNOWN_RECORD_IDS_PER_FRAME]
        truncated = truncated or len(known_record_ids) < len(delta.known_record_ids)
        seq = ledger.next_seq()
        payload = RetrievalContextPayload(
            seq=seq,
            source=source,
            status=status,
            statusReason=status_reason,
            toolName=tool_name,
            toolCallId=tool_call_id,
            records=records,
            knownRecordIds=known_record_ids,
            cumulativeBlocks=delta.cumulative_blocks,
            cumulativeRecords=delta.cumulative_records,
            errorMessage=error_message[:_MAX_ERROR_MESSAGE_CHARS] if error_message else None,
            truncated=(truncated or seq >= MAX_FRAMES_PER_RUN) or None,
        )
        for event in context.formatter.retrieval_context(context, payload=payload):
            await context.event_sink.write(event)
    except Exception:
        logger.exception(
            "retrieval_context: dropping frame for run %s", getattr(context, "run_id", None),
        )


def _bounded_records(
    records: list[RetrievedRecordPayload],
) -> tuple[list[RetrievedRecordPayload], bool]:
    truncated = len(records) > MAX_RECORDS_PER_FRAME
    kept = records[:MAX_RECORDS_PER_FRAME]
    for record in kept:
        if len(record.blockIndices) > MAX_BLOCK_INDICES_PER_RECORD:
            record.blockIndices = record.blockIndices[:MAX_BLOCK_INDICES_PER_RECORD]
            truncated = True
        for fetched in record.fetched:
            if fetched.shownBlocks and len(fetched.shownBlocks) > MAX_BLOCK_INDICES_PER_RECORD:
                fetched.shownBlocks = fetched.shownBlocks[:MAX_BLOCK_INDICES_PER_RECORD]
                truncated = True
    return kept, truncated


def prefetch_status(
    *, scheduled: bool, result: PrefetchResult | None,
) -> tuple[RetrievalStatus, str | None, str | None]:
    """`(status, status_reason, error_message)` for the prefetch frame, so a
    consumer can tell "prefetch found nothing" from "prefetch never ran"."""
    if not scheduled:
        return "skipped", "mode_no_prefetch", None
    if result is None:
        return "skipped", "followup", None
    if result.is_empty:
        if result.error_message:
            return "error", None, result.error_message
        return "empty", None, None
    return "ok", None, None


async def emit_prefetch_retrieval_context(
    context: AgentContext, *, scheduled: bool, result: PrefetchResult | None,
) -> None:
    """Exactly one prefetch frame per opted-in turn, even when nothing was
    retrieved. Call after prefetch results are merged into `tool_state`."""
    status, reason, error_message = prefetch_status(scheduled=scheduled, result=result)
    await emit_retrieval_context(
        context,
        source="prefetch",
        status=status,
        status_reason=reason,
        error_message=error_message,
        always=True,
    )


__all__ = [
    "RetrievalContextLedger",
    "RetrievalDelta",
    "emit_prefetch_retrieval_context",
    "emit_retrieval_context",
    "prefetch_status",
]
