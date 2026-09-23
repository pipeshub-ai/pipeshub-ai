"""Turns the retrieval accumulators in `AgentContext.tool_state` into opt-in
CUSTOM `retrieval_context` stream frames.

Retrieval tools mutate (or replace) `final_results`,
`virtual_record_id_to_result` and `known_record_ids`; `fetch_record` also
records `fetch_render_outcomes` when the flag is on. Instead of teaching every
tool to report, the ledger diffs that shared state after prefetch and after
every tool call and reports only what is new — a future retrieval tool is
covered without touching this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.protocol.retrieval_context import (
    FetchedRangePayload,
    RetrievalContextPayload,
    RetrievalSource,
    RetrievalStatus,
    RetrievedRecordPayload,
)
from app.modules.retrieval.context.ordering import RELEVANCE_RANK_KEY
from app.modules.retrieval.context.ranking import RERANK_SCORE_KEY

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.chat_modes.prefetch import PrefetchResult

_MAX_ERROR_MESSAGE_CHARS = 500

BlockKey = tuple[str, int | None]
FetchKey = tuple[str, int, int]


@dataclass
class _RecordDelta:
    virtual_record_id: str
    record_id: str | None = None
    record_name: str | None = None
    web_url: str | None = None
    block_indices: list[int] = field(default_factory=list)
    summary_hit: bool = False
    max_score: float | None = None
    relevance_rank: int | None = None
    rerank_score: float | None = None
    fetched: list[FetchedRangePayload] = field(default_factory=list)

    def observe_score(self, score: object) -> None:
        self.max_score = _max_score(self.max_score, score)

    def observe_rerank_score(self, score: object) -> None:
        self.rerank_score = _max_score(self.rerank_score, score)

    def observe_rank(self, rank: object) -> None:
        if isinstance(rank, int) and not isinstance(rank, bool):
            self.relevance_rank = rank if self.relevance_rank is None else min(self.relevance_rank, rank)

    def to_payload(self) -> RetrievedRecordPayload:
        return RetrievedRecordPayload(
            virtualRecordId=self.virtual_record_id,
            recordId=self.record_id,
            recordName=self.record_name,
            webUrl=self.web_url,
            blockIndices=self.block_indices,
            summaryHit=self.summary_hit,
            maxScore=self.max_score,
            relevanceRank=self.relevance_rank,
            rerankScore=self.rerank_score,
            fetched=self.fetched,
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


def _max_score(current: float | None, score: object) -> float | None:
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return current
    return float(score) if current is None else max(current, float(score))


def _str_or_none(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


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
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def take_delta(self, tool_state: Mapping[str, Any]) -> RetrievalDelta:
        records_map: Mapping[str, Any] = tool_state.get("virtual_record_id_to_result") or {}
        deltas: dict[str, _RecordDelta] = {}
        self._collect_blocks(tool_state.get("final_results") or [], records_map, deltas)
        self._collect_fetches(tool_state.get("fetch_render_outcomes") or {}, records_map, deltas)
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
            raw_index = block.get("block_index")
            block_index = int(raw_index) if isinstance(raw_index, int) else None
            key: BlockKey = (virtual_record_id, block_index)
            if key in self._seen_blocks:
                continue
            self._seen_blocks.add(key)
            metadata = block.get("metadata")
            delta = self._delta_for(
                virtual_record_id, records_map, deltas,
                metadata if isinstance(metadata, Mapping) else None,
            )
            if block_index is None:
                delta.summary_hit = True
            else:
                delta.block_indices.append(block_index)
            delta.observe_score(block.get("score"))
            delta.observe_rank(block.get(RELEVANCE_RANK_KEY))
            delta.observe_rerank_score(block.get(RERANK_SCORE_KEY))

    def _collect_fetches(
        self,
        outcomes: Mapping[str, Any],
        records_map: Mapping[str, Any],
        deltas: dict[str, _RecordDelta],
    ) -> None:
        for record_id, entries in outcomes.items():
            for entry in entries or ():
                fetched = FetchedRangePayload(
                    startBlock=int(entry.get("startBlock") or 0),
                    blocksRendered=int(entry.get("blocksRendered") or 0),
                    complete=bool(entry.get("complete")),
                )
                key: FetchKey = (str(record_id), fetched.startBlock, fetched.blocksRendered)
                if key in self._seen_fetches:
                    continue
                self._seen_fetches.add(key)
                virtual_record_id = _str_or_none(entry.get("virtualRecordId")) or str(record_id)
                delta = self._delta_for(virtual_record_id, records_map, deltas)
                delta.record_id = delta.record_id or str(record_id)
                delta.fetched.append(fetched)

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
    writes nothing (unless `always`), so ordinary tool calls cost no bytes."""
    if not context.include_retrieval_context or context.event_sink is None:
        return
    ledger = context.retrieval_ledger
    delta = ledger.take_delta(context.tool_state)
    if delta.is_empty and status == "ok" and not always:
        return
    payload = RetrievalContextPayload(
        seq=ledger.next_seq(),
        source=source,
        status=status,
        statusReason=status_reason,
        toolName=tool_name,
        toolCallId=tool_call_id,
        records=delta.records,
        knownRecordIds=delta.known_record_ids,
        cumulativeBlocks=delta.cumulative_blocks,
        cumulativeRecords=delta.cumulative_records,
        errorMessage=error_message[:_MAX_ERROR_MESSAGE_CHARS] if error_message else None,
    )
    for event in context.formatter.retrieval_context(context, payload=payload):
        await context.event_sink.write(event)


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
