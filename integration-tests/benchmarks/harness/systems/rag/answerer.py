"""Naive and advanced RAG over PipesHub's own index.

Both read the Qdrant points PipesHub wrote for the benchmark KB, embed queries
with the embedding model PipesHub indexed with, and answer with the same LLM,
so the only differences from PipesHub are the pipeline's control flow and its
retrieval options. Every LLM call (query transforms included) is recorded.

Pipeline: [transform] -> retrieve per query -> merge -> [rerank] -> top_k
-> [small-to-big] -> numbered sources -> answer with [n] citations.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.harness.config import FRAMES_SNAPSHOT, ModelPrice
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.llm.client import ChatMessage, LLMClient, LLMRequest, ResolvedModel
from benchmarks.harness.models import AskItem, CallUsage, Citation, IngestManifest, Prediction, RankedList, RetrievedChunk, SystemFailure
from benchmarks.harness.systems.base import AdapterCapabilities, CorpusIngestor, PreparedCorpus, RankedRetriever
from benchmarks.harness.systems.baselines.answering import (
    ANSWER_MAX_TOKENS,
    call_usage,
    dated_system_prompt,
    usage_fields,
)
from benchmarks.harness.systems.rag.rerank import DEFAULT_RERANKER, CrossEncoderReranker
from benchmarks.harness.systems.rag.retrieval import Chunk, RetrievalMode, interleave, rrf_merge
from benchmarks.harness.systems.rag.transforms import decompose, expand

logger = logging.getLogger(__name__)

RAG_ANSWER_PROMPT_VERSION = "rag-answer-v1"
_CHARS_PER_TOKEN = 4
_DEFAULT_CONTEXT_TOKENS = 128_000
_RESERVED_TOKENS = 8_000
_MARKER = re.compile(r"\[(\d+)\]")
_SYSTEM_PROMPT = (
    "You answer factual questions using the numbered sources provided, which are passages from "
    "Wikipedia articles. Base your answer on the sources and cite every source you rely on with "
    "its number in square brackets, e.g. [3]. Reason carefully, then state the final answer "
    "explicitly and concisely on the last line."
)

Transform = Literal["none", "expansion", "decomposition"]


class RagOptions(BaseModel):
    """`SystemConfig.options` for `naive_rag` / `advanced_rag`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # `pipeshub`: the points PipesHub indexed. `standard`: the baselines' own
    # collection (`RunConfig.standard_index`).
    index: Literal["pipeshub", "standard"] = "pipeshub"
    retrieval: RetrievalMode = "dense"
    top_k: int = Field(default=50, ge=1, le=500)
    # Points retrieved per query before merging/reranking; defaults to top_k,
    # or 2 * top_k when reranking so the reranker has something to choose from.
    candidate_k: int | None = Field(default=None, ge=1, le=1000)
    rerank: bool = False
    reranker: str = DEFAULT_RERANKER
    transform: Transform = "none"
    n_queries: int = Field(default=3, ge=1, le=10)
    max_subquestions: int = Field(default=5, ge=1, le=10)
    # Replace the chunks of the N highest-ranked articles with the full article.
    small_to_big: int = Field(default=0, ge=0, le=20)
    context_tokens: int | None = Field(default=None, ge=1000)

    @property
    def depth(self) -> int:
        return self.candidate_k or (self.top_k * 2 if self.rerank else self.top_k)


@dataclass(frozen=True)
class Source:
    """One numbered entry in the prompt."""

    url: str
    record_id: str
    virtual_record_id: str
    title: str
    text: str
    block_index: int | None


@dataclass(frozen=True)
class RecordRef:
    record_id: str
    url: str
    title: str


def fit_sources(sources: Sequence[Source], max_tokens: int) -> tuple[list[Source], bool]:
    budget = max_tokens * _CHARS_PER_TOKEN
    kept: list[Source] = []
    for source in sources:
        cost = len(source.text) + len(source.title) + 16
        if cost > budget:
            return kept, True
        kept.append(source)
        budget -= cost
    return kept, False


def render_sources(sources: Sequence[Source]) -> str:
    return "\n\n".join(f"[{i}] {s.title}\n{s.text}" for i, s in enumerate(sources, start=1))


def cited_sources(answer: str, sources: Sequence[Source]) -> list[Citation]:
    cited = sorted({int(n) for n in _MARKER.findall(answer or "")})
    return [
        Citation(
            display_index=n, record_id=sources[n - 1].record_id, virtual_record_id=sources[n - 1].virtual_record_id,
            record_name=sources[n - 1].title, content=sources[n - 1].text, block_nums=[] if sources[n - 1].block_index is None else [sources[n - 1].block_index],
        )
        for n in cited if 1 <= n <= len(sources)
    ]


class ChunkSearcher(Protocol):
    def search(self, query: str, mode: RetrievalMode, limit: int) -> list[Chunk]: ...


IndexFactory = Callable[[IngestManifest], tuple[ChunkSearcher, dict[str, RecordRef]]]


class RagAnswerer:
    capabilities = AdapterCapabilities(ingests_corpus=True, citations=True, ranked_search=True)

    def __init__(
        self,
        system_id: str,
        llm: LLMClient,
        model: ResolvedModel,
        options: RagOptions,
        *,
        ingestor: CorpusIngestor,
        index_factory: IndexFactory,
        corpus: CorpusView,
        price: ModelPrice | None = None,
        current_time: datetime = FRAMES_SNAPSHOT,
        reranker: CrossEncoderReranker | None = None,
    ) -> None:
        self.system_id = system_id
        self._llm = llm
        self._model = model
        self._options = options
        self._ingestor = ingestor
        self._index_factory = index_factory
        self._corpus = corpus
        self._price = price
        self._current_time = current_time
        self._reranker = reranker or (CrossEncoderReranker(options.reranker) if options.rerank else None)
        self._index: tuple[ChunkSearcher, dict[str, RecordRef]] | None = None
        self._lock = threading.Lock()

    def ingestor(self) -> CorpusIngestor | None:
        return self._ingestor

    def retriever(self) -> RankedRetriever | None:
        return self

    def _ensure_index(self, prepared: PreparedCorpus) -> tuple[ChunkSearcher, dict[str, RecordRef]]:
        with self._lock:
            if self._index is None:
                if prepared.ingest is None:
                    raise RuntimeError(f"{self.system_id}: corpus was not ingested")
                self._index = self._index_factory(prepared.ingest)
            return self._index

    def _context_budget(self) -> int:
        if self._options.context_tokens:
            return self._options.context_tokens
        return (self._model.context_length or _DEFAULT_CONTEXT_TOKENS) - _RESERVED_TOKENS

    def _queries(self, question: str, calls: list[CallUsage]) -> list[str]:
        o = self._options
        if o.transform == "expansion":
            extra, response = expand(self._llm, self._model, question, o.n_queries, self._current_time)
            calls.append(call_usage(response, "query_expansion"))
            return [question, *extra]
        if o.transform == "decomposition":
            subs, response = decompose(self._llm, self._model, question, o.max_subquestions, self._current_time)
            calls.append(call_usage(response, "query_decomposition"))
            return [question, *subs]
        return [question]

    def retrieve(
        self, question: str, index: ChunkSearcher, calls: list[CallUsage], queries_out: list[str] | None = None,
    ) -> list[Chunk]:
        o = self._options
        queries = self._queries(question, calls)
        if queries_out is not None:
            queries_out.extend(queries)
        ranked = [index.search(q, o.retrieval, o.depth) for q in queries]
        if len(ranked) == 1:
            merged = ranked[0]
        elif o.transform == "decomposition":
            merged = interleave(ranked)
        else:
            merged = rrf_merge(ranked)
        if self._reranker is not None:
            # Cap what the cross-encoder scores: with query expansion every
            # variant contributes `depth` candidates, so the pool (and the
            # rerank cost) grows with the number of queries.
            started = time.monotonic()
            ranked = self._reranker.rerank(question, merged[:o.depth], o.top_k)
            logger.debug("reranked %d candidates in %.1fs", min(len(merged), o.depth), time.monotonic() - started)
            return ranked
        return merged[:o.top_k]

    def _sources(self, chunks: Sequence[Chunk], records: dict[str, RecordRef]) -> list[Source]:
        known = [(c, records[c.virtual_record_id]) for c in chunks if c.virtual_record_id in records]
        expanded: dict[str, Source] = {}
        if self._options.small_to_big:
            for chunk, ref in known:
                if len(expanded) >= self._options.small_to_big:
                    break
                if ref.url not in expanded:
                    expanded[ref.url] = Source(ref.url, ref.record_id, chunk.virtual_record_id, ref.title, self._corpus.text(ref.url), None)
        sources: list[Source] = []
        emitted: set[str] = set()
        for chunk, ref in known:
            if ref.url in expanded:
                if ref.url not in emitted:
                    emitted.add(ref.url)
                    sources.append(expanded[ref.url])
                continue
            sources.append(Source(ref.url, ref.record_id, chunk.virtual_record_id, ref.title, chunk.text, chunk.block_index))
        return sources

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction:
        started = time.monotonic()
        base = Prediction(system=self.system_id, question_id=item.question_id, repeat=repeat)
        calls: list[CallUsage] = []
        queries: list[str] = []
        try:
            index, records = self._ensure_index(prepared)
            chunks = self.retrieve(item.prompt, index, calls, queries)
            sources, truncated = fit_sources(self._sources(chunks, records), self._context_budget())
            response = self._llm.complete(LLMRequest(
                model=self._model, max_tokens=ANSWER_MAX_TOKENS, prompt_version=RAG_ANSWER_PROMPT_VERSION,
                messages=(
                    ChatMessage(role="system", content=dated_system_prompt(_SYSTEM_PROMPT, self._current_time)),
                    ChatMessage(role="user", content=f"Sources:\n\n{render_sources(sources)}\n\nQuestion: {item.prompt}"),
                ),
            ))
            calls.append(call_usage(response, "answer"))
        except Exception as exc:  # noqa: BLE001 — recorded on the prediction and scored FALSE
            logger.warning("%s q%s failed: %s", self.system_id, item.question_id, exc)
            return base.model_copy(update={
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": SystemFailure(kind="llm", message=str(exc)[:1000]),
                "queries": queries,
                **usage_fields(calls, self._price),
            })
        return base.model_copy(update={
            "answer": response.text, "citations": cited_sources(response.text, sources),
            "queries": queries,
            "retrieved": [
                RetrievedChunk(url=records[c.virtual_record_id].url, virtual_record_id=c.virtual_record_id, block_index=c.block_index, score=c.score)
                for c in chunks if c.virtual_record_id in records
            ],
            "context_urls": list(dict.fromkeys(s.url for s in sources)), "context_truncated": truncated,
            "latency_ms": int((time.monotonic() - started) * 1000),
            **usage_fields(calls, self._price),
        })

    def ranked_search(self, item: AskItem, prepared: PreparedCorpus, k: int) -> RankedList:
        index, records = self._ensure_index(prepared)
        chunks = index.search(item.prompt, self._options.retrieval, max(k * 5, self._options.depth))
        urls = [records[c.virtual_record_id].url for c in chunks if c.virtual_record_id in records]
        return RankedList(system=self.system_id, question_id=item.question_id, ranked_refs=list(dict.fromkeys(urls))[:k])
