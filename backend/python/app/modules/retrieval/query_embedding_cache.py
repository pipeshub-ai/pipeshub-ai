"""Query embeddings reused for a short while, so the same query searches with the same vector.

A remote embedding service can return a slightly different vector for the same text
on each call. Two passages that are almost equally close to the query then swap
places from one call to the next, and a repeated search returns different results
and scores. Reusing the vector for a few minutes makes a repeated query answer the
same, and saves the embedding call.

The key is the org, the embedding configuration's hash and the text sent to the
model, so a change of model or provider is never answered from the cache. Bounded
in size and age; in memory, per process.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict

_DEFAULT_TTL_SECONDS = 600.0
_DEFAULT_MAX_ENTRIES = 2048


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 else default


class QueryEmbeddingCache:
    def __init__(self, ttl_seconds: float | None = None, max_entries: int | None = None) -> None:
        self.ttl = _env_number("QUERY_EMBEDDING_CACHE_SECONDS", _DEFAULT_TTL_SECONDS) if ttl_seconds is None else ttl_seconds
        self.max_entries = (
            int(_env_number("QUERY_EMBEDDING_CACHE_ENTRIES", _DEFAULT_MAX_ENTRIES)) if max_entries is None else max_entries
        )
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, list[float]]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self.ttl > 0 and self.max_entries > 0

    def get(self, org_id: str, config_hash: str | None, text: str) -> list[float] | None:
        if not self.enabled or config_hash is None:
            return None
        key = (org_id, config_hash, text)
        found = self._entries.get(key)
        if found is None:
            return None
        stored_at, vector = found
        if time.monotonic() - stored_at > self.ttl:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return list(vector)

    def put(self, org_id: str, config_hash: str | None, text: str, vector: list[float]) -> None:
        if not self.enabled or config_hash is None or not vector:
            return
        key = (org_id, config_hash, text)
        self._entries[key] = (time.monotonic(), list(vector))
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
