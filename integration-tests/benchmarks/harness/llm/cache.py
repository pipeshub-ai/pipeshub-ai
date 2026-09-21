"""SQLite-backed response cache for judge calls.

Judgments are deterministic in (model, prompt, parameters), so re-grading or
re-reporting a run costs nothing. Answer generation is never cached — repeats
exist precisely to measure its variance.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from benchmarks.harness.llm.client import EmbeddingResponse, LLMClient, LLMRequest, LLMResponse, ResolvedModel

_SCHEMA = "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body TEXT NOT NULL)"


def cache_key(request: LLMRequest) -> str:
    payload = {
        "model": request.model.litellm_model,
        "messages": [m.model_dump() for m in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "prompt_version": request.prompt_version,
        "reasoning_effort": request.model.reasoning_effort,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class LLMCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._local = threading.local()
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=30)
            self._local.conn = conn
        return conn

    def get(self, key: str) -> LLMResponse | None:
        row = self._connection().execute("SELECT body FROM responses WHERE key = ?", (key,)).fetchone()
        return LLMResponse.model_validate_json(row[0]) if row else None

    def put(self, key: str, response: LLMResponse) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO responses (key, body) VALUES (?, ?)",
                (key, response.model_dump_json()),
            )


class CachedLLMClient:
    def __init__(self, inner: LLMClient, cache: LLMCache) -> None:
        self._inner = inner
        self._cache = cache

    def embed(self, model: ResolvedModel, texts: list[str]) -> EmbeddingResponse:
        return self._inner.embed(model, texts)

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not request.cacheable:
            return self._inner.complete(request)
        key = cache_key(request)
        hit = self._cache.get(key)
        if hit is not None:
            return hit.model_copy(update={"cached": True})
        response = self._inner.complete(request)
        self._cache.put(key, response)
        return response
