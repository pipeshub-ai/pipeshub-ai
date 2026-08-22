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
``_ensure_initialized`` uses an ``asyncio.Lock`` so concurrent callers in the
same event loop initialise the model exactly once.  Model construction is
off-loaded to ``asyncio.to_thread`` so the (30–60 s cold-start) load never
blocks the event loop.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import List, Optional

from app.services.vector_db.models import SparseVector, to_generic_sparse_vector
from app.utils.logger import create_logger

logger = create_logger("sparse_embeddings")

_MODEL_NAME = "Qdrant/bm25"
# SparseTextEmbedding in fastembed 0.5.1 pops local_files_only before forwarding
# it to Bm25, so cache-only loads have to go through HF_HUB_OFFLINE. That env
# var is process-global; serialize mutations so concurrent embedders cannot
# leak "1" into other threads or leave it set after restore.
_HF_OFFLINE_ENV_LOCK = threading.Lock()


def _hf_hub_offline_enabled() -> bool:
    return os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in {"1", "true", "yes"}


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
        self._lock: Optional[asyncio.Lock] = None

    # ------------------------------------------------------------------
    # Internal initialisation
    # ------------------------------------------------------------------

    def _get_lock(self) -> asyncio.Lock:
        """Return (creating if needed) the per-instance lock.

        The lock cannot be created in ``__init__`` when the instance is
        constructed outside a running event loop.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _build_model(self):
        """Synchronous model construction — runs inside a thread pool.

        huggingface_hub otherwise probes the network even when Qdrant/bm25 is
        already cached, which can hang indexing forever on a blocked network.
        Try a cache-only load first; download only if the model is missing
        and HF_HUB_OFFLINE is not set.
        """
        from fastembed import SparseTextEmbedding  # type: ignore

        def _construct_from_cache():
            old = os.environ.get("HF_HUB_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                return SparseTextEmbedding(model_name=self._model_name)
            finally:
                if old is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = old

        with _HF_OFFLINE_ENV_LOCK:
            explicitly_offline = _hf_hub_offline_enabled()
            try:
                return _construct_from_cache()
            except Exception:
                if explicitly_offline:
                    raise
                logger.info(
                    "Sparse model '%s' not in local cache; downloading from Hub",
                    self._model_name,
                )
                return SparseTextEmbedding(model_name=self._model_name)

    async def _ensure_initialized(self) -> object:
        """Ensure the model is loaded; return it."""
        if self._model is not None:
            return self._model
        async with self._get_lock():
            if self._model is None:
                self._model = await asyncio.to_thread(self._build_model)
                logger.info(
                    f"Sparse embedder initialised: model='{self._model_name}', "
                    "backend=fastembed.SparseTextEmbedding"
                )
        return self._model

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
