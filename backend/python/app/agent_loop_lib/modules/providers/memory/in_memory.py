from __future__ import annotations

import uuid
from typing import Any

from app.agent_loop_lib.modules.providers.memory.base import (
    MemoryProvider,
    MemoryResult,
    MemoryScope,
)


class InMemoryProvider(MemoryProvider):
    """Non-persistent in-process memory store.

    FOR TESTING AND DEVELOPMENT ONLY.
    Uses substring search — no embeddings, no vector similarity.
    Production: use mem0, Graphiti, Zep, or a vector store instead.
    """

    def __init__(self) -> None:
        # {memory_id: {"content": str, "metadata": dict, "scope": MemoryScope|None}}
        self._store: dict[str, dict[str, Any]] = {}

    async def add(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        scope: MemoryScope | None = None,
    ) -> str:
        memory_id = str(uuid.uuid4())
        self._store[memory_id] = {
            "content": content,
            "metadata": metadata or {},
            "scope": scope,
        }
        return memory_id

    async def search(
        self,
        query: str,
        top_k: int = 10,
        scope: MemoryScope | None = None,
    ) -> list[MemoryResult]:
        results = []
        for mid, entry in self._store.items():
            if query.lower() in entry["content"].lower():
                if scope is None or _scope_matches(entry["scope"], scope):
                    results.append(MemoryResult(
                        id=mid,
                        content=entry["content"],
                        metadata=entry["metadata"],
                        score=1.0,
                    ))
        return results[:top_k]

    async def get(self, memory_id: str) -> MemoryResult | None:
        entry = self._store.get(memory_id)
        if entry is None:
            return None
        return MemoryResult(
            id=memory_id,
            content=entry["content"],
            metadata=entry["metadata"],
            score=1.0,
        )

    async def delete(self, memory_id: str) -> None:
        self._store.pop(memory_id, None)

    async def clear(self, scope: MemoryScope | None = None) -> None:
        if scope is None:
            self._store.clear()
        else:
            to_delete = [
                mid for mid, entry in self._store.items()
                if _scope_matches(entry["scope"], scope)
            ]
            for mid in to_delete:
                del self._store[mid]


def _scope_matches(stored: MemoryScope | None, query: MemoryScope) -> bool:
    if stored is None:
        return False  # unscoped entries are not matched by a scope filter
    if query.agent_id is not None and stored.agent_id != query.agent_id:
        return False
    if query.user_id is not None and stored.user_id != query.user_id:
        return False
    if query.session_id is not None and stored.session_id != query.session_id:
        return False
    if query.team_id is not None and stored.team_id != query.team_id:
        return False
    return True
