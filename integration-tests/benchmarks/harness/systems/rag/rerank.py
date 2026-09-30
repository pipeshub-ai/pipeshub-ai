"""Cross-encoder reranking (local model, so it adds latency but no API tokens)."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import requests

from benchmarks.harness.systems.rag.retrieval import Chunk

# The cross-encoder PipesHub itself ships (`modules/reranker/reranker.py`).
# Pinned to CPU: torch's MPS backend hangs when inference runs off the main
# thread, and this model reranks a question's candidates in seconds there.
DEFAULT_RERANKER = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_DEVICE = "cpu"
_MAX_LENGTH = 512
_BATCH_SIZE = 32
# One request waits behind every other asker's on a single-threaded server.
_REMOTE_TIMEOUT_S = 600.0


class CrossEncoderReranker:
    def __init__(self, model_name: str = DEFAULT_RERANKER, device: str = _DEVICE) -> None:
        self.model_name = model_name
        self._device = device
        self._model: Any = None
        self._lock = threading.Lock()

    def _ensure(self) -> Any:  # noqa: ANN401
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name, max_length=_MAX_LENGTH, device=self._device)
        return self._model

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        """Raw cross-encoder logits, one per text, in input order."""
        if not texts:
            return []
        # One model instance; the lock serialises concurrent askers on it.
        with self._lock:
            scores = self._ensure().predict(
                [(query, t) for t in texts], batch_size=_BATCH_SIZE, show_progress_bar=False,
            )
        return [float(x) for x in scores]

    def rerank(self, query: str, chunks: Sequence[Chunk], top_k: int) -> list[Chunk]:
        scores = self.scores(query, [c.text for c in chunks])
        order = sorted(range(len(chunks)), key=lambda i: -scores[i])
        return [chunks[i] for i in order[:top_k]]


class RemoteReranker:
    """A cross-encoder behind `rerank_server`, for a model too slow on CPU:
    the server can run it on a GPU, which in-process worker threads cannot."""

    def __init__(self, url: str, model_name: str, *, timeout_s: float = _REMOTE_TIMEOUT_S) -> None:
        self._url = url.rstrip("/")
        self._timeout_s = timeout_s
        served = requests.get(f"{self._url}/health", timeout=timeout_s).json().get("model")
        if served != model_name:
            raise ValueError(f"rerank server at {url} serves {served!r}, the config asks for {model_name!r}")
        self.model_name = model_name

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        resp = requests.post(f"{self._url}/rerank", json={"query": query, "texts": list(texts)}, timeout=self._timeout_s)
        resp.raise_for_status()
        scores = [0.0] * len(texts)
        for row in resp.json():
            scores[int(row["index"])] = float(row["score"])
        return scores

    def rerank(self, query: str, chunks: Sequence[Chunk], top_k: int) -> list[Chunk]:
        scores = self.scores(query, [c.text for c in chunks])
        order = sorted(range(len(chunks)), key=lambda i: -scores[i])
        return [chunks[i] for i in order[:top_k]]
