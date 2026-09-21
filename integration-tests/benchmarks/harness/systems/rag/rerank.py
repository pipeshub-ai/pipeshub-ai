"""Cross-encoder reranking (local model, so it adds latency but no API tokens)."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from benchmarks.harness.systems.rag.retrieval import Chunk

# The cross-encoder PipesHub itself ships (`modules/reranker/reranker.py`).
# Pinned to CPU: torch's MPS backend hangs when inference runs off the main
# thread, and this model reranks a question's candidates in seconds there.
DEFAULT_RERANKER = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_DEVICE = "cpu"
_MAX_LENGTH = 512
_BATCH_SIZE = 32


class CrossEncoderReranker:
    def __init__(self, model_name: str = DEFAULT_RERANKER) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _ensure(self) -> Any:  # noqa: ANN401
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name, max_length=_MAX_LENGTH, device=_DEVICE)
        return self._model

    def rerank(self, query: str, chunks: Sequence[Chunk], top_k: int) -> list[Chunk]:
        if not chunks:
            return []
        # One model instance; the lock serialises concurrent askers on it.
        with self._lock:
            scores = self._ensure().predict(
                [(query, c.text) for c in chunks], batch_size=_BATCH_SIZE, show_progress_bar=False,
            )
        order = sorted(range(len(chunks)), key=lambda i: -float(scores[i]))
        return [chunks[i] for i in order[:top_k]]
