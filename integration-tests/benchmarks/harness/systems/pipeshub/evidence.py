"""Reading back the text of PipesHub's Qdrant points, and reconstructing what
PipesHub's agent was shown from its retrieval trace.

The stream's `retrieval_context` frames name blocks, not text: which
`virtualRecordId`s reached the model, with which block indices, whether a
record-level (no block index) hit was shown, and which block ranges a fetch
rendered. The text is read back from the points PipesHub indexed.

Point kinds (`app/modules/transformers/vectorstore.py`): one `isBlock` point
per text block; sentence/window points (`isBlock: false`, same `blockIndex`)
that copy a block's text — used only for oversized blocks that have no whole
point; the record summary (`isRecordSummary`); and table points that carry no
`blockIndex` at all.

A trace hit without a block index is a summary hit or a table row: both are
record-level points, so every block-less point of the record is included.
When unsure, this errs towards including text the model may not have seen —
an answer is flagged as answered from memory only when the evidence lacks it.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from benchmarks.harness.evidence import EvidenceSourceError, StoreBackedBuilder, captured, empty, unavailable
from benchmarks.harness.models import Evidence, EvidencePassage, Prediction, RetrievedRecordRef

EVIDENCE_SOURCE = "pipeshub_vector_store"
_VRID_KEY = "metadata.virtualRecordId"
_SCROLL_PAGE = 256
_MAX_RECORD_LEVEL_POINTS = 200
_CACHED_RECORDS = 1024


class PointSource(Protocol):
    def payloads(self, virtual_record_id: str) -> list[Mapping[str, Any]]: ...


class QdrantPointSource:
    """Every point payload of one record, scrolled from PipesHub's collection."""

    def __init__(self, client_factory: Callable[[], Any], collection: str) -> None:
        self._client_factory = client_factory
        self._collection = collection

    def payloads(self, virtual_record_id: str) -> list[Mapping[str, Any]]:
        try:
            from qdrant_client import models

            client = self._client_factory()
            scope = models.Filter(must=[
                models.FieldCondition(key=_VRID_KEY, match=models.MatchValue(value=virtual_record_id)),
            ])
            payloads: list[Mapping[str, Any]] = []
            offset = None
            while True:
                points, offset = client.scroll(
                    collection_name=self._collection, scroll_filter=scope, limit=_SCROLL_PAGE,
                    offset=offset, with_payload=True, with_vectors=False,
                )
                payloads.extend(p.payload or {} for p in points)
                if offset is None:
                    return payloads
        except Exception as exc:  # noqa: BLE001 — any client/transport failure means "try again later"
            raise EvidenceSourceError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc


@dataclass
class RecordPoints:
    blocks: dict[int, str] = field(default_factory=dict)
    windows: dict[int, list[str]] = field(default_factory=dict)
    summary: str | None = None
    record_level: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, payloads: Iterable[Mapping[str, Any]]) -> RecordPoints:
        points = cls()
        unindexed: list[tuple[str, str]] = []
        for payload in payloads:
            meta = payload.get("metadata") or {}
            text = str(payload.get("page_content") or "")
            if not text:
                continue
            block = meta.get("blockIndex")
            if meta.get("isRecordSummary"):
                points.summary = text
            elif isinstance(block, int) and not isinstance(block, bool):
                if meta.get("isBlock") is True:
                    points.blocks[block] = text
                elif not meta.get("isBlockGroup"):
                    points.windows.setdefault(block, []).append(text)
            else:
                unindexed.append((str(meta.get("blockId") or ""), text))
        # Scroll order is by point id; sorting makes the passage order stable.
        points.record_level = [text for _id, text in sorted(unindexed)][:_MAX_RECORD_LEVEL_POINTS]
        for texts in points.windows.values():
            texts.sort()
        return points

    def block_text(self, index: int) -> str | None:
        if index in self.blocks:
            return self.blocks[index]
        windows = self.windows.get(index)
        return "\n".join(windows) if windows else None


class RecordPointsCache:
    """Parsed points per record, shared by every builder reading PipesHub's
    collection in a run: the same gold articles recur across systems."""

    def __init__(self, source: PointSource, *, size: int = _CACHED_RECORDS) -> None:
        self._source = source
        self._size = size
        self._cache: OrderedDict[str, RecordPoints] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, virtual_record_id: str) -> RecordPoints:
        with self._lock:
            if virtual_record_id in self._cache:
                self._cache.move_to_end(virtual_record_id)
                return self._cache[virtual_record_id]
        points = RecordPoints.of(self._source.payloads(virtual_record_id))
        with self._lock:
            self._cache[virtual_record_id] = points
            if len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return points


def _wanted_blocks(ref: RetrievedRecordRef) -> list[int]:
    wanted = list(ref.block_indices)
    for fetched in ref.fetched:
        if fetched.shown_blocks:
            wanted.extend(fetched.shown_blocks)
        else:
            wanted.extend(range(fetched.start_block, fetched.start_block + fetched.blocks_rendered))
    return list(dict.fromkeys(wanted))


class TraceEvidenceBuilder(StoreBackedBuilder):
    """PipesHub's evidence, from its `retrieval_context` trace."""

    source = EVIDENCE_SOURCE

    def __init__(self, points: RecordPointsCache) -> None:
        super().__init__()
        self._points = points

    def _build(self, prediction: Prediction) -> Evidence:
        if prediction.trace is None:
            return unavailable(self.source, "no retrieval trace was recorded")
        refs = [
            ref for event in sorted(prediction.trace.retrieval_events, key=lambda e: e.seq)
            for ref in event.records if ref.reached_model
        ]
        if not refs:
            return empty(self.source)
        passages: list[EvidencePassage] = []
        seen: set[tuple[str, int | str]] = set()
        missing = 0
        for ref in refs:
            vrid = ref.virtual_record_id
            points = self._points.get(vrid)
            name = ref.record_name or vrid
            for index in _wanted_blocks(ref):
                if (vrid, index) in seen:
                    continue
                seen.add((vrid, index))
                text = points.block_text(index)
                if text is None:
                    missing += 1
                    continue
                passages.append(EvidencePassage(header=f"{name} (block {index})\n", text=text))
            if ref.summary_hit and points.summary and (vrid, "summary") not in seen:
                seen.add((vrid, "summary"))
                passages.append(EvidencePassage(header=f"{name} (summary)\n", text=points.summary))
            # Table rows are block-less points, so a block-less hit or a
            # rendered fetch may have shown any of them.
            covers_tables = ref.summary_hit or any(f.blocks_rendered > 0 for f in ref.fetched)
            if covers_tables and (vrid, "tables") not in seen:
                seen.add((vrid, "tables"))
                passages.extend(EvidencePassage(header=f"{name} (table)\n", text=t) for t in points.record_level)
        if not passages:
            return unavailable(
                self.source, f"the vector store has no text for the {len(refs)} record hit(s) in the trace",
            )
        return captured(passages, self.source, reconstructed=True).model_copy(update={"missing_blocks": missing})
