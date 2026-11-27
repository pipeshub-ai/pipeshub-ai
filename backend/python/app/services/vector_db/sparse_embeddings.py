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
blocks the event loop.  A failed build is shared, not re-run: callers within
the retry window raise the recorded outcome at once instead of each
re-running fastembed's multi-minute download under the lock (issue #5904).
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, List, Optional

from app.services.vector_db.models import SparseVector, to_generic_sparse_vector
from app.utils.env_utils import env_positive_int, hf_hub_offline
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = create_logger("sparse_embeddings")

_MODEL_NAME = "Qdrant/bm25"


def _init_attempt_timeout_seconds() -> float:
    """Deadline for one model build, cache-only or download.

    fastembed's own download path retries three times with 9 s and 27 s
    backoffs, so one cold attempt on an unreachable Hub costs ~90 s; this
    bounds what a caller waits for it.
    """
    return env_positive_int("SPARSE_EMBEDDER_INIT_TIMEOUT_SECONDS", 120)


def _failure_retry_seconds() -> int:
    """How long a failed build is remembered before another is attempted."""
    return env_positive_int("SPARSE_EMBEDDER_FAILURE_RETRY_SECONDS", 60)


class SparseEmbedder:
    """Thread-safe, lazy-initialised BM25 sparse embedder.

    Uses ``fastembed.SparseTextEmbedding`` as the sole backend.

    Usage::

        embedder = SparseEmbedder()
        query_vec   = await embedder.embed_query("what is AI?")
        doc_vecs    = await embedder.embed_documents(["text 1", "text 2"])
    """

    def __init__(self, model_name: str = _MODEL_NAME, clock: Callable[[], float] = time.monotonic) -> None:
        self._model_name = model_name
        self._model: Optional[object] = None
        self._lock: Optional[asyncio.Lock] = None
        self._clock = clock
        # The last failed build and when another may be attempted: ``(exception,
        # deadline)``. Every caller arriving before the deadline raises the
        # recorded outcome instead of re-running the download (see #5904).
        self._init_failure: Optional[tuple[BaseException, float]] = None

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

    def _raise_if_failure_is_fresh(self) -> None:
        """Raise the recorded build failure while it still counts.

        Re-raising the stored exception instance from every waiter is fine —
        they share one outcome rather than each paying for a new download.
        """
        if self._init_failure is None:
            return
        exception, retry_at = self._init_failure
        if self._clock() < retry_at:
            raise exception

    async def _ensure_initialized(self) -> object:
        """Ensure the model is loaded; return it.

        A failed build is remembered for ``SPARSE_EMBEDDER_FAILURE_RETRY_SECONDS``
        (default 60): a caller arriving in that window raises the recorded
        outcome at once — without the lock, without a new download — and the
        waiters queued behind a failed attempt drain the same way. One attempt
        is bounded by ``SPARSE_EMBEDDER_INIT_TIMEOUT_SECONDS`` (default 120);
        ``asyncio.wait_for`` returns control but cannot stop the worker thread,
        so a timed-out download may still finish in the background and be
        discarded — the retry builds again from scratch.
        """
        if self._model is not None:
            return self._model
        self._raise_if_failure_is_fresh()
        async with self._get_lock():
            if self._model is not None:
                return self._model
            # A waiter queued behind the caller that just failed must not
            # start its own build: the recorded failure is still fresh.
            self._raise_if_failure_is_fresh()
            try:
                self._model = await asyncio.wait_for(
                    asyncio.to_thread(self._build_model),
                    timeout=_init_attempt_timeout_seconds(),
                )
            except Exception as exc:
                retry_at = self._clock() + _failure_retry_seconds()
                self._init_failure = (exc, retry_at)
                logger.warning(
                    "Sparse embedder failed to load model '%s'; retrying after %ss: %r",
                    self._model_name,
                    _failure_retry_seconds(),
                    exc,
                )
                raise
            self._init_failure = None
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
