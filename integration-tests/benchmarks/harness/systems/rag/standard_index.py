"""The baselines' own Qdrant collection, built the standard way: recursive
token-window chunking over the article text, the same embedding model as
PipesHub, and BM25 sparse vectors with Qdrant's IDF modifier.

It exists so the headline comparison (baselines on PipesHub's own index) can
be checked against a stock ingestion pipeline. Idempotent: point ids derive
from (url, chunk index) and indexed articles are checkpointed, so an
interrupted build resumes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from benchmarks.harness.config import StandardIndexConfig
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.llm.client import LLMClient, ResolvedModel
from benchmarks.harness.models import CorpusManifest, IndexReport, IngestedRecord, IngestManifest
from benchmarks.harness.systems.rag.retrieval import hybrid_points
from benchmarks.harness.store import atomic_write_text
from benchmarks.harness.systems.base import PreparedCorpus
from benchmarks.harness.systems.rag.retrieval import SPARSE_MODEL, Chunk, QueryEncoder, RetrievalMode

logger = logging.getLogger(__name__)

_TOKENIZER = "cl100k_base"  # text-embedding-3-* tokenizer
_POINT_NAMESPACE = uuid.UUID("3f1c6f5e-8a51-4c49-9d5e-1d7a2b0c9e11")


def point_id(url: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{url}#{chunk_index}"))


def collection_name(config: StandardIndexConfig, corpus_version: str) -> str:
    return f"{config.collection_prefix}_{corpus_version[:12]}_c{config.chunk_tokens}o{config.chunk_overlap}"


def chunk_text(text: str, chunk_tokens: int, overlap: int) -> list[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=_TOKENIZER, chunk_size=chunk_tokens, chunk_overlap=overlap,
    )
    return [c for c in splitter.split_text(text) if c.strip()]


class StandardIndexIngestor:
    def __init__(
        self,
        client: Any,  # noqa: ANN401 — qdrant_client.QdrantClient
        llm: LLMClient,
        embedding: ResolvedModel,
        corpus: CorpusView,
        config: StandardIndexConfig,
        *,
        system_id: str,
        cache_dir: Path,
    ) -> None:
        self._client = client
        self._llm = llm
        self._embedding = embedding
        self._corpus = corpus
        self._config = config
        self._system_id = system_id
        self._cache_dir = cache_dir / "standard_index"
        self._sparse: Any = None
        self._sparse_lock = threading.Lock()

    def _checkpoint(self, collection: str) -> Path:
        return self._cache_dir / f"{collection}.done.json"

    def _ensure_collection(self, collection: str) -> bool:
        """Create the collection if missing; returns whether it was created."""
        from qdrant_client import models

        created = not self._client.collection_exists(collection)
        if created:
            dim = len(self._llm.embed(self._embedding, ["dimension probe"]).vectors[0])
            self._client.create_collection(
                collection_name=collection,
                vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
                sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
            logger.info("created %s (dense dim %d, sparse bm25 + IDF)", collection, dim)
        # Idempotent; `url` is what the post-build coverage check facets on.
        self._client.create_payload_index(collection, field_name="url", field_schema=models.PayloadSchemaType.KEYWORD)
        return created

    def _sparse_docs(self, texts: Sequence[str]) -> list[Any]:
        with self._sparse_lock:
            if self._sparse is None:
                from fastembed import SparseTextEmbedding

                self._sparse = SparseTextEmbedding(model_name=SPARSE_MODEL)
            return list(self._sparse.embed(list(texts)))

    def _index_batch(self, collection: str, batch: Sequence[tuple[str, int, str, str]]) -> None:
        from qdrant_client import models

        texts = [text for _url, _i, _title, text in batch]
        dense = self._llm.embed(self._embedding, texts).vectors
        sparse = self._sparse_docs(texts)
        self._client.upsert(collection_name=collection, wait=True, points=[
            models.PointStruct(
                id=point_id(url, i),
                vector={
                    "dense": dense[n],
                    "sparse": models.SparseVector(indices=sparse[n].indices.tolist(), values=sparse[n].values.tolist()),
                },
                payload={"url": url, "chunk_index": i, "title": title, "text": text},
            )
            for n, (url, i, title, text) in enumerate(batch)
        ])

    def prepare(self, manifest: CorpusManifest) -> PreparedCorpus:
        collection = collection_name(self._config, manifest.corpus_version)
        created = self._ensure_collection(collection)
        path = self._checkpoint(collection)
        if created and path.exists():
            # The checkpoint outlived its collection (e.g. the vector store was
            # wiped); trusting it would leave the new collection empty.
            logger.warning("%s was recreated; discarding its stale checkpoint", collection)
            path.unlink()
        done: set[str] = set(json.loads(path.read_text())) if path.exists() else set()
        todo = [d for d in manifest.documents if d.canonical_url not in done]
        logger.info("%s: %d articles indexed, %d to go", collection, len(done), len(todo))
        started = time.monotonic()
        step = max(1, self._config.embed_batch_size // 8)
        with ThreadPoolExecutor(self._config.embed_workers) as pool:
            for start in range(0, len(todo), step):
                docs = todo[start:start + step]
                chunks = [
                    (d.canonical_url, i, d.title, text)
                    for d in docs
                    for i, text in enumerate(chunk_text(
                        self._corpus.text(d.canonical_url), self._config.chunk_tokens, self._config.chunk_overlap,
                    ))
                ]
                size = self._config.embed_batch_size
                list(pool.map(lambda b: self._index_batch(collection, b), [chunks[j:j + size] for j in range(0, len(chunks), size)]))
                done.update(d.canonical_url for d in docs)
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(path, json.dumps(sorted(done)))
                if (start // step) % 20 == 0:
                    logger.info("%s: %d/%d articles (%.0fs)", collection, len(done), len(manifest.documents), time.monotonic() - started)
        return PreparedCorpus(
            system=self._system_id, corpus_version=manifest.corpus_version,
            ingest=IngestManifest(
                system=self._system_id, kb_id=collection, corpus_version=manifest.corpus_version,
                base_url="qdrant",
                records=[IngestedRecord(record_id=d.canonical_url, record_name=d.title, canonical_url=d.canonical_url) for d in manifest.documents if d.canonical_url in done],
            ),
        )

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport:
        indexed = {r.canonical_url for r in prepared.ingest.records} if prepared.ingest else set()
        gold = [d.canonical_url for d in manifest.documents if d.tier == "gold"]
        return IndexReport(
            total=len(manifest.documents), status_counts={"COMPLETED": len(indexed)},
            gold_total=len(gold), gold_indexed=sum(1 for url in gold if url in indexed),
            unindexed_urls=[url for url in gold if url not in indexed],
        )


class StandardChunkIndex:
    """`hybrid_points` over the dedicated collection — same query as the
    PipesHub-index path, without the record scope filter."""

    def __init__(self, client: Any, collection: str, encoder: QueryEncoder) -> None:  # noqa: ANN401
        self._client = client
        self._collection = collection
        self._encoder = encoder

    def search(self, query: str, mode: RetrievalMode, limit: int) -> list[Chunk]:
        points = hybrid_points(self._client, self._collection, self._encoder, query, mode, limit)
        return [
            Chunk(p.payload["url"], int(p.payload["chunk_index"]), p.payload.get("text", ""), float(p.score or 0.0))
            for p in points if p.payload and p.payload.get("url")
        ]
