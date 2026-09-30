"""Wire payload for the opt-in CUSTOM `retrieval_context` stream frame.

Carries identifiers only — which records and blocks reached the model after
prefetch or a tool call — never block text, so the frame stays small and
adds no content exposure beyond what the answer's citations already carry.
Field names are camelCase to match the wire contract external clients (e.g.
evaluation harnesses) parse, same convention as `ArtifactSSEPayload`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RETRIEVAL_CONTEXT_EVENT_NAME = "retrieval_context"
RETRIEVAL_CONTEXT_SCHEMA_VERSION = 1

RetrievalSource = Literal["prefetch", "tool"]
RetrievalStatus = Literal["ok", "empty", "skipped", "error"]


class FetchedRangePayload(BaseModel):
    """One `fetch_record` render of a record: where it started and how much
    of the record actually fit the model's budget."""

    model_config = ConfigDict(extra="forbid")

    startBlock: int
    blocksRendered: int
    complete: bool
    # Block indices the model could read in full. `blocksRendered` counts
    # units, and one table unit can hold hundreds of rows.
    shownBlocks: list[int] = Field(default_factory=list)


class RetrievedRecordPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    virtualRecordId: str
    recordId: str | None = None
    recordName: str | None = None
    webUrl: str | None = None
    blockIndices: list[int] = Field(default_factory=list)
    # A record-level (summary) hit carries no block index.
    summaryHit: bool = False
    maxScore: float | None = None
    # 1-based position of the record in what the model was shown, most
    # relevant first. None when the record was not ranked (e.g. a fetch).
    relevanceRank: int | None = None
    # Best reranker score among the record's blocks; None when not reranked.
    rerankScore: float | None = None
    fetched: list[FetchedRangePayload] = Field(default_factory=list)


class RetrievalContextPayload(BaseModel):
    """Delta since the previous frame of the same run: `records` lists only
    blocks/records not reported before; `cumulative*` are running totals."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: int = RETRIEVAL_CONTEXT_SCHEMA_VERSION
    seq: int
    source: RetrievalSource
    status: RetrievalStatus = "ok"
    statusReason: str | None = None
    toolName: str | None = None
    toolCallId: str | None = None
    records: list[RetrievedRecordPayload] = Field(default_factory=list)
    # Record ids surfaced without content (navigate / lookup / list_files).
    knownRecordIds: list[str] = Field(default_factory=list)
    cumulativeBlocks: int = 0
    cumulativeRecords: int = 0
    errorMessage: str | None = None

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)
