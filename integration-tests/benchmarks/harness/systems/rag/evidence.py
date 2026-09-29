"""Rebuilding a RAG baseline's prompt sources after the fact, for runs asked
before the answerer recorded its evidence.

A prediction keeps the chunks it put in context (`retrieved`: record id,
block index, URL, in context order, after reranking) and whether fitting cut
them (`context_truncated`, `context_urls`). The chunk texts are read back
from the collection the system searched — PipesHub's points or the standard
index — and run through the answerer's own `assemble_sources`, so
small-to-big articles come back whole from the corpus exactly as they were
sent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.evidence import EvidenceSourceError, StoreBackedBuilder, captured, empty, unavailable
from benchmarks.harness.models import Evidence, Prediction, RetrievedChunk
from benchmarks.harness.systems.pipeshub.evidence import RecordPointsCache
from benchmarks.harness.systems.rag.answerer import RecordRef, assemble_sources, source_passages
from benchmarks.harness.systems.rag.retrieval import Chunk
from benchmarks.harness.systems.rag.standard_index import point_id

EVIDENCE_SOURCE = "rag_reconstructed"
ChunkKey = tuple[str, int | None]


class ChunkTexts(Protocol):
    def texts(self, chunks: Sequence[RetrievedChunk]) -> dict[ChunkKey, str]: ...


class PipesHubChunkTexts:
    """A chunk is a whole block (sentence hits were folded into their block
    at retrieval). A block-less chunk was one record-level point — summary or
    table row — and which one is not recorded, so all of them stand in."""

    def __init__(self, points: RecordPointsCache) -> None:
        self._points = points

    def texts(self, chunks: Sequence[RetrievedChunk]) -> dict[ChunkKey, str]:
        found: dict[ChunkKey, str] = {}
        for chunk in chunks:
            points = self._points.get(chunk.virtual_record_id)
            if chunk.block_index is not None:
                text = points.block_text(chunk.block_index)
            else:
                text = "\n".join([*([points.summary] if points.summary else []), *points.record_level]) or None
            if text is not None:
                found[(chunk.virtual_record_id, chunk.block_index)] = text
        return found


class StandardChunkTexts:
    """Standard-index chunks are addressed by (url, chunk index), which is
    also how their point ids are derived."""

    def __init__(self, client_factory: Callable[[], Any], collection: str) -> None:
        self._client_factory = client_factory
        self._collection = collection

    def texts(self, chunks: Sequence[RetrievedChunk]) -> dict[ChunkKey, str]:
        ids = [point_id(c.virtual_record_id, c.block_index) for c in chunks if c.block_index is not None]
        if not ids:
            return {}
        try:
            points = self._client_factory().retrieve(
                collection_name=self._collection, ids=ids, with_payload=True, with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001 — any client/transport failure means "try again later"
            raise EvidenceSourceError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        return {
            (str(p.payload["url"]), int(p.payload["chunk_index"])): str(p.payload.get("text") or "")
            for p in points if p.payload and p.payload.get("url") is not None
        }


class RagEvidenceBuilder(StoreBackedBuilder):
    source = EVIDENCE_SOURCE

    def __init__(self, texts: ChunkTexts, corpus: CorpusView, small_to_big: int) -> None:
        super().__init__()
        self._texts = texts
        self._corpus = corpus
        self._small_to_big = small_to_big

    def _title(self, url: str) -> str:
        document = self._corpus.document(url)
        return document.title if document is not None else url

    def _build(self, prediction: Prediction) -> Evidence:
        if not prediction.retrieved:
            if prediction.context_urls:
                return unavailable(self.source, "the prediction recorded no retrieved chunks")
            return empty(self.source)
        texts = self._texts.texts(prediction.retrieved)
        chunks = [
            (Chunk(c.virtual_record_id, c.block_index, texts.get((c.virtual_record_id, c.block_index), ""), c.score),
             RecordRef("", c.url, self._title(c.url)))
            for c in prediction.retrieved
        ]
        sources = assemble_sources(chunks, self._small_to_big, self._corpus.text)
        notes = []
        if prediction.context_truncated:
            # Fitting keeps a prefix; the prefix ends where the recorded
            # context stops naming the article.
            shown = set(prediction.context_urls)
            cut = next((i for i, s in enumerate(sources) if s.url not in shown), len(sources))
            sources = sources[:cut]
            notes.append("the prompt was truncated at ask time; its end is approximated from context_urls")
        missing = sum(1 for s in sources if not s.text)
        if set(s.url for s in sources) != set(prediction.context_urls):
            notes.append("the rebuilt sources name different articles than the prompt did")
        if not any(s.text for s in sources):
            return unavailable(self.source, f"the vector store has no text for the {len(sources)} sources")
        evidence = captured(source_passages(sources), self.source, reconstructed=True)
        return evidence.model_copy(update={"missing_blocks": missing, "reason": "; ".join(notes) or None})
