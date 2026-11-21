"""Unified sparse embedding adapter.

Wraps fastembed ``SparseTextEmbedding`` with a consistent API used by all
three code paths (VectorStore, RetrievalService, IndexingPipeline).

Why a separate module
---------------------
The old code copy-pasted three almost-identical lazy-init blocks across
vectorstore.py, retrieval_service.py and indexing/run.py, each calling
different methods on the underlying model.  This module normalises the
surface to two typed async methods so every call site looks the same.

Thread safety
-------------
Concurrent callers in the same event loop share one initialization attempt,
including its failure. Model construction runs in a worker thread so the
cold-start load does not block the event loop.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from app.services.vector_db.models import SparseVector, to_generic_sparse_vector
from app.utils.env_utils import hf_hub_offline
from app.utils.logger import create_logger

logger = create_logger("sparse_embeddings")

_MODEL_NAME = "Qdrant/bm25"


class SparseEmbedder:
    """Thread-safe, lazy-initialised BM25 sparse embedder.

    Uses ``fastembed.SparseTextEmbedding`` as the sole backend.

    Usage::

        embedder = SparseEmbedder()
        query_vec   = await embedder.embed_query("what is AI?")
        doc_vecs    = await embedder.embed_documents(["text 1", "text 2"])
    """

    def __init__(self, model_name: str = _MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: Optional[object] = None
        self._initialization: asyncio.Task[object] | None = None

    # ------------------------------------------------------------------
    # Internal initialisation
    # ------------------------------------------------------------------

    def _build_model(self):
        """Synchronous model construction — runs inside a thread pool.

        huggingface_hub otherwise probes the network even when Qdrant/bm25 is
        already cached, which can hang indexing forever on a blocked network.
        Try a cache-only load first; download only if the model is missing
        and HF_HUB_OFFLINE is not set.
        """
        from fastembed import SparseTextEmbedding  # type: ignore

        # fastembed passes local_files_only through to Bm25 since 0.7. Toggling
        # HF_HUB_OFFLINE here would not work: huggingface_hub reads it once, at import.
        try:
            return SparseTextEmbedding(model_name=self._model_name, local_files_only=True)
        except Exception:
            if hf_hub_offline():
                raise
            logger.info(
                "Sparse model '%s' not in local cache; downloading from Hub",
                self._model_name,
            )
            return SparseTextEmbedding(model_name=self._model_name)

    async def _load_model(self) -> object:
        model = await asyncio.to_thread(self._build_model)
        self._model = model
        logger.info(
            "Sparse embedder initialised: model='%s', backend=fastembed.SparseTextEmbedding",
            self._model_name,
        )
        return model

    def _finish_initialization(self, task: asyncio.Task[object]) -> None:
        if self._initialization is task:
            self._initialization = None
        # Retrieve a failure even if every waiting request was cancelled.
        if not task.cancelled():
            task.exception()

    async def _ensure_initialized(self) -> object:
        """Return the cached model or join the current initialization attempt."""
        if self._model is not None:
            return self._model
        task = self._initialization
        if task is None or task.done():
            task = asyncio.create_task(self._load_model())
            self._initialization = task
            task.add_done_callback(self._finish_initialization)
        # Cancelling a request cannot stop the worker thread and must not start
        # a second load while that thread is still running.
        return await asyncio.shield(task)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed_query(self, text: str) -> SparseVector:
        """Embed a single query text and return a generic ``SparseVector``.

        Uses ``query_embed`` for query-optimised BM25 scoring.
        """
        model = await self._ensure_initialized()
        raw = await asyncio.to_thread(
            lambda: next(iter(model.query_embed(text)))  # type: ignore[attr-defined]
        )
        return to_generic_sparse_vector(raw)

    async def embed_documents(self, texts: List[str]) -> List[SparseVector]:
        """Embed a list of texts and return generic ``SparseVector`` instances.

        Uses ``embed`` (batched passage embedding).
        """
        if not texts:
            return []
        model = await self._ensure_initialized()
        raw_list = await asyncio.to_thread(
            lambda: list(model.embed(texts))  # type: ignore[attr-defined]
        )
        return [to_generic_sparse_vector(r) for r in raw_list]


# ---------------------------------------------------------------------------
# Module-level singleton — shared by VectorStore, RetrievalService and
# IndexingPipeline so the model is loaded only once per process.
# ---------------------------------------------------------------------------

_default_embedder: Optional[SparseEmbedder] = None
_default_embedder_lock: Optional[asyncio.Lock] = None


async def get_default_sparse_embedder() -> SparseEmbedder:
    """Return the process-wide ``SparseEmbedder`` singleton, creating it once."""
    global _default_embedder, _default_embedder_lock
    if _default_embedder is not None:
        return _default_embedder
    if _default_embedder_lock is None:
        _default_embedder_lock = asyncio.Lock()
    async with _default_embedder_lock:
        if _default_embedder is None:
            _default_embedder = SparseEmbedder()
    return _default_embedder
